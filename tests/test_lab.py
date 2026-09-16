import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from conversation_lab.config import load_config
from conversation_lab.metrics import percentile, recognition_errors, wav_info
from conversation_lab.providers import HTTP, Mock, Worker
from conversation_lab.report import build_report, read_lines, summarize
from conversation_lab.runner import execute, run, write_json
from conversation_lab.server import make_server

ROOT = Path(__file__).resolve().parents[1]


class MetricsTests(unittest.TestCase):
    def test_percentile_interpolation_and_empty(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([100, 200], 95), 195)
        self.assertEqual(percentile([3], 95), 3)

    def test_korean_normalization(self):
        result = recognition_errors("안녕하세요, 여러분!", "안녕하세요 여러분")
        self.assertEqual(result["cer"], 0)
        self.assertEqual(result["wer"], 0)

    def test_insertions_can_exceed_one(self):
        self.assertEqual(recognition_errors("가", "가나다")["cer"], 2)
        with self.assertRaises(ValueError):
            recognition_errors("...", "안녕")

    def test_failures_not_in_latency_and_corpus_is_weighted(self):
        rows = [dict(experiment="x", phase="warm", synthetic=False, status="ok",
                     metrics={"request_ms": 10, **recognition_errors("가", "나")}),
                dict(experiment="x", phase="warm", synthetic=False, status="ok",
                     metrics={"request_ms": 20, **recognition_errors("가나다", "가나다")}),
                dict(experiment="x", phase="warm", synthetic=False, status="error", metrics={})]
        result = summarize(rows)[0]
        self.assertAlmostEqual(result["failure_rate"], 1 / 3)
        self.assertEqual(result["metrics"]["request_ms"]["median"], 15)
        self.assertEqual(result["metrics"]["corpus_cer"], 0.25)


class ConfigTests(unittest.TestCase):
    def test_shipped_configs(self):
        for name in ("demo.json", "local.json"):
            cfg, root = load_config(ROOT / "configs" / name)
            self.assertEqual(root, ROOT)
            self.assertTrue(cfg["experiments"])

    def test_bad_case_reference_and_remote_endpoint(self):
        original = json.loads((ROOT / "configs/demo.json").read_text())
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            original["root"] = str(ROOT)
            original["providers"]["remote"] = {"kind": "llm", "adapter": "chat_sse", "url": "https://example.com/chat"}
            write_json(path, original)
            with self.assertRaisesRegex(ValueError, "allow_remote"):
                load_config(path)
            del original["providers"]["remote"]
            original["experiments"][0]["tts"] = "mock-chat"
            write_json(path, original)
            with self.assertRaisesRegex(ValueError, "wrong provider"):
                load_config(path)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def chat_server(self, events, sse=False):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()
                for event in events:
                    payload = json.dumps(event, ensure_ascii=False)
                    self.wfile.write((("data: " if sse else "") + payload + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(.005)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}/chat"

    def test_ollama_thinking_excluded_and_server_metrics(self):
        url = self.chat_server([{"message": {"thinking": "hidden"}}, {"message": {"content": "안녕"}},
                                {"done": True, "eval_count": 2, "eval_duration": 100000000}])
        p = HTTP({"adapter": "ollama", "kind": "llm", "model": "test", "url": url}, self.root, self.root / "log")
        result = p.run({"text": "hello"}, self.root / "unused")
        self.assertEqual(result["text"], "안녕")
        self.assertEqual(result["metrics"]["server_tokens_per_s"], 20)
        self.assertGreater(result["metrics"]["first_text_ms"], 0)

    def test_sse_and_truncated_stream(self):
        url = self.chat_server([{"choices": [{"delta": {"content": "안녕"}, "finish_reason": None}]},
                                {"choices": [{"delta": {}, "finish_reason": "stop"}]}], True)
        p = HTTP({"adapter": "chat_sse", "kind": "llm", "model": "test", "url": url}, self.root, self.root / "log")
        self.assertEqual(p.run({"text": "hi"}, self.root / "unused")["text"], "안녕")
        p.config["url"] = self.chat_server([{"message": {"content": "incomplete"}}])
        p.config["adapter"] = "ollama"
        with self.assertRaisesRegex(RuntimeError, "completion"):
            p.run({"text": "hi"}, self.root / "unused")

    def test_persistent_worker_and_timeout(self):
        script = self.root / "worker.py"
        script.write_text('import json,sys,time\nfor line in sys.stdin:\n r=json.loads(line)\n if r["op"]=="load": print("{}",flush=True)\n else: time.sleep(5)\n')
        p = Worker({"adapter": "worker", "kind": "llm", "command": ["{python}", str(script)], "timeout_s": .2}, self.root, self.root / "log")
        try:
            p.start()
            with self.assertRaises(TimeoutError):
                p.run({"text": "hello"}, self.root / "unused")
            self.assertIsNotNone(p.process.poll())
        finally:
            p.close()

    def test_worker_reuses_loaded_process(self):
        script = self.root / "success.py"
        script.write_text('import json,sys\nn=0\nfor line in sys.stdin:\n r=json.loads(line)\n if r["op"]=="load": print("{}",flush=True)\n else:\n  n+=1\n  print(json.dumps({"text":str(n),"metrics":{}}),flush=True)\n')
        p = Worker({"adapter": "worker", "kind": "llm", "command": ["{python}", str(script)]}, self.root, self.root / "log")
        try:
            p.start()
            self.assertEqual(p.run({"text": "first"}, self.root / "x")["text"], "1")
            self.assertEqual(p.run({"text": "second"}, self.root / "x")["text"], "2")
        finally:
            p.close()

    def test_recorded_pipeline_uses_transcript_without_reference_leak(self):
        (self.root / "audio").mkdir()
        tts = Mock({"adapter": "mock", "kind": "tts"}, self.root, self.root / "log")
        recording = self.root / "input.wav"
        tts.run({"text": "fixture"}, recording)
        test = self
        class STT:
            metadata = {"synthetic": True}
            def run(self, case, output):
                test.assertNotIn("reference", case)
                return {"text": "인식된 질문", "metrics": {}}
        class LLM:
            metadata = {"synthetic": True}
            def run(self, case, output):
                test.assertEqual(case["text"], "인식된 질문")
                test.assertNotIn("reference", case)
                return {"text": "응답", "metrics": {}}
        result = execute({"mode": "pipeline"}, {"id": "x", "audio": str(recording), "reference": "원래 질문"},
                         {"stt": STT(), "llm": LLM(), "tts": tts}, self.root, "test")
        self.assertTrue(result["synthetic"])
        self.assertIn("stt.cer", result["metrics"])
        self.assertGreaterEqual(result["metrics"]["pipeline_total_ms"], result["metrics"]["tts.request_ms"])


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with patch("conversation_lab.runner.hardware", return_value={"test": True}):
            cls.folder, cls.failures = run(ROOT / "configs/demo.json", cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_run_report_and_artifacts(self):
        self.assertEqual(self.failures, 0)
        samples = read_lines(self.folder / "samples.jsonl")
        self.assertEqual(len(samples), 108)
        self.assertTrue(all(s["synthetic"] for s in samples))
        self.assertEqual(len(read_lines(self.folder / "warmups.jsonl")), 4)
        for sample in samples:
            if "tts" in sample["outputs"]:
                info = wav_info(self.folder / sample["outputs"]["tts"]["audio_path"])
                self.assertEqual(info["audio_duration_s"], 1)
        self.assertTrue((self.folder / "report.md").exists())
        self.assertTrue((self.folder / "samples.csv").exists())

    def test_setup_errors_persist_and_nonzero(self):
        cfg = json.loads((ROOT / "configs/demo.json").read_text())
        cfg["root"] = str(ROOT)
        cfg["experiments"] = cfg["experiments"][:1]
        cfg["providers"]["tone-a"] = {"kind": "tts", "adapter": "worker", "command": ["does-not-exist-xyz"]}
        path = Path(self.temp.name) / "bad.json"
        write_json(path, cfg)
        with patch("conversation_lab.runner.hardware", return_value={}):
            folder, failed = run(path, self.temp.name)
        self.assertEqual(failed, 36)
        self.assertTrue(all(s["status"] == "error" for s in read_lines(folder / "samples.jsonl")))

    def test_dashboard_blinding_audio_and_review_validation(self):
        server = make_server(self.folder, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(base + "/api/listen") as response:
                items = json.load(response)
            self.assertNotIn("experiment", items[0])
            sample = next(i for i in items if i["audio"])
            with urllib.request.urlopen(base + "/audio/" + sample["id"]) as response:
                self.assertTrue(response.read().startswith(b"RIFF"))
            request = urllib.request.Request(base + "/api/review", json.dumps({"id": sample["id"], "reviewer": "test", "scores": {"naturalness": 5}}).encode(), {"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            request.add_header("Origin", "https://example.com")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_review_save_and_latest_rating_wins(self):
        # A temporary non-model fixture exercises storage; it is never published as a benchmark.
        folder = Path(self.temp.name) / "review-fixture"
        folder.mkdir(exist_ok=True)
        sample = dict(read_lines(self.folder / "samples.jsonl")[0])
        sample.update(synthetic=False, experiment="unit-test-fixture")
        (folder / "samples.jsonl").write_text(json.dumps(sample) + "\n", encoding="utf-8")
        server = make_server(folder, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with urllib.request.urlopen(base + "/api/listen") as response:
                item = json.load(response)[0]
            for score in (2, 5):
                request = urllib.request.Request(base + "/api/review", json.dumps({"id": item["id"], "reviewer": "tester", "scores": {"naturalness": score}}).encode(), {"Content-Type": "application/json"})
                with urllib.request.urlopen(request) as response:
                    self.assertTrue(json.load(response)["saved"])
            build_report(folder)
            summary = json.loads((folder / "summary.json").read_text())
            rating = summary["human_ratings"]["unit-test-fixture"]["naturalness"]
            self.assertEqual(rating, {"n": 1, "mean": 5})
            self.assertEqual(len(read_lines(folder / "reviews.jsonl")), 2)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
