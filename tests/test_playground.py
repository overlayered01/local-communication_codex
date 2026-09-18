import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
import base64
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from conversation_lab.playground import make_playground
from conversation_lab.providers import create_provider


class PlaygroundTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        received = self.received

        class Ollama(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"models":[{"name":"test-model"},{"name":"nomic-embed-text"}]}')

            def do_POST(self):
                received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"message":{"content":"Hello"},"done":true}\n')

        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
        threading.Thread(target=self.ollama.serve_forever, daemon=True).start()
        self.temp = tempfile.TemporaryDirectory()
        config = Path(self.temp.name) / "config.json"
        config.write_text(json.dumps({"schema_version": 1, "providers": {
            "llm": {"kind": "llm", "adapter": "ollama", "url": f"http://127.0.0.1:{self.ollama.server_port}/api/chat"}
        }}), encoding="utf-8")
        self.server = make_playground(config, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.ollama.shutdown()
        self.ollama.server_close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        headers = headers or {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.urlopen(urllib.request.Request(self.base + path,
            data=json.dumps(body).encode() if body is not None else None, headers=headers), timeout=5)

    def test_discover_and_generate_pipeline_with_history_without_dataset(self):
        with self.request("/api/models") as response:
            models = json.load(response)["models"]
        self.assertEqual(models[0]["label"], "test-model")
        self.assertEqual(len(models), 1)
        self.assertNotIn("config", models[0])
        self.server.app.models["test-tts"] = {"kind": "tts", "ready": True,
            "config": {"kind": "tts", "adapter": "mock"}}
        history = [{"role": "user", "content": "Remember this"}, {"role": "assistant", "content": "OK"}]
        with self.request("/api/generate", {"mode": "pipeline", "text": "Hi", "messages": history,
                "llm": models[0]["id"], "tts": "test-tts", "temperature": 0.3}) as response:
            self.assertEqual(response.status, 202)
            key = json.load(response)["id"]
        for _ in range(100):
            with self.request("/api/jobs/" + key) as response:
                job = json.load(response)
            if job["status"] != "running":
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["text"], "Hello")
        self.assertEqual(self.received[0]["messages"][1:3], history)
        self.assertEqual(self.received[0]["options"]["temperature"], 0.3)
        with self.request(job["audio"]) as response:
            with wave.open(io.BytesIO(response.read())) as audio:
                self.assertGreater(audio.getnframes(), 0)

    def test_reject_cross_origin_unknown_model_and_concurrent_request(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/models", headers={"Origin": "https://evil.example"})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/generate", {"mode": "chat", "text": "Hi", "llm": "unknown"})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.server.app.discover()
        key = next(iter(self.server.app.models))
        self.server.app.busy.acquire()
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/generate", {"mode": "chat", "text": "Hi", "llm": key})
            self.assertEqual(error.exception.code, 409)
            error.exception.close()
        finally:
            self.server.app.busy.release()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/audio/../../config.json")
        self.assertEqual(error.exception.code, 404)
        error.exception.close()

    def test_voice_pipeline_uses_recording_and_rejects_invalid_audio(self):
        app = self.server.app
        app.discover()
        llm = next(iter(app.models))
        app.models["stt"] = {"kind": "stt", "ready": True, "config": {"kind": "stt", "adapter": "worker"}}
        app.models["tts"] = {"kind": "tts", "ready": True, "config": {"kind": "tts", "adapter": "mock"}}
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            output.writeframes(b"\0\0" * 16000)
        payload = {"mode": "voice", "llm": llm, "tts": "tts", "stt": "stt",
                   "audio_base64": base64.b64encode(buffer.getvalue()).decode()}

        class Recognizer:
            def start(self):
                pass

            def run(self, case, output):
                assert set(case) == {"audio"}
                assert Path(case["audio"]).is_file()
                return {"text": "Recognized speech"}

            def close(self):
                pass

        def provider(cfg, root, log):
            return Recognizer() if cfg["kind"] == "stt" else create_provider(cfg, root, log)

        with patch("conversation_lab.playground.create_provider", side_effect=provider):
            key = app.submit(payload)["id"]
            for _ in range(100):
                if app.jobs[key]["status"] != "running":
                    break
                time.sleep(0.01)
        self.assertEqual(app.jobs[key]["status"], "done", app.jobs[key])
        self.assertEqual(app.jobs[key]["transcript"], "Recognized speech")
        self.assertEqual(self.received[0]["messages"][-1]["content"], "Recognized speech")
        self.assertIn("audio", app.jobs[key])
        with self.assertRaises(ValueError):
            app.submit({**payload, "audio_base64": "not a wav"})



if __name__ == "__main__":
    unittest.main()
