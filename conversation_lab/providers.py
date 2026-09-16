"""Transport adapters. Engine dependencies stay in separate worker environments."""
import array
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path


def ms(start):
    return (time.perf_counter() - start) * 1000


class Provider:
    def __init__(self, config, root, log_path):
        self.config, self.root, self.log_path = config, root, log_path
        self.timeout = config.get("timeout_s", 120)
        self.metadata = {"adapter": config["adapter"], "model": config.get("model"),
                         "synthetic": config["adapter"] == "mock"}

    def start(self):
        return self.metadata

    def close(self):
        pass


class Mock(Provider):
    """A plumbing fixture. Tone output and fake text are NEVER model evidence."""
    def run(self, case, output):
        if self.config["kind"] == "llm":
            return {"text": "이 응답은 연결 검증용 모의 응답입니다. " + case["text"],
                    "metrics": {}, "synthetic": True}
        rate = 16000
        frequency = self.config.get("frequency", 440)
        samples = array.array("h", (int(1600 * math.sin(2 * math.pi * frequency * n / rate))
                                   for n in range(rate)))
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(output), "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(samples.tobytes())
        return {"audio_path": str(output), "metrics": {}, "synthetic": True}


class HTTP(Provider):
    def close(self):
        if self.config["adapter"] == "ollama" and self.config.get("unload_after", False) and getattr(self, "used", False):
            try:
                with self.request({"model": self.config["model"], "messages": [], "stream": False, "keep_alive": 0}) as response:
                    response.read()
            except Exception as exc:
                self.log_path.write_text(f"Unload failed: {exc}", encoding="utf-8")

    def request(self, payload):
        headers = {"Content-Type": "application/json"}
        if env := self.config.get("api_key_env"):
            if not os.environ.get(env):
                raise ValueError(f"Missing credential environment variable: {env}")
            headers["Authorization"] = "Bearer " + os.environ[env]
        request = urllib.request.Request(self.config["url"],
                                         json.dumps(payload).encode("utf-8"), headers)
        # No ambient HTTP proxy: local benchmark traffic must remain local.
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            request, timeout=self.timeout)

    def run(self, case, output):
        self.used = True
        adapter = self.config["adapter"]
        params = dict(self.config.get("params", {}))
        if adapter == "tts_http":
            payload = {**params, "model": self.config.get("model", "local"),
                       "input": case["text"], "response_format": "wav"}
            started = time.perf_counter()
            with self.request(payload) as response, Path(output).open("wb") as f:
                while block := response.read(65536):
                    f.write(block)
                    if ms(started) > self.timeout * 1000:
                        raise TimeoutError("TTS response exceeded total deadline")
            # Receiving bytes is not equivalent to receiving playable audio.
            return {"audio_path": str(output), "metrics": {}}
        messages = list(case.get("messages", []))
        if system := self.config.get("system"):
            messages.insert(0, {"role": "system", "content": system})
        messages.append({"role": "user", "content": case["text"]})
        payload = {**params, "model": self.config["model"], "messages": messages, "stream": True}
        started = time.perf_counter()
        first = None
        pieces, metrics = [], {}
        completed = False
        with self.request(payload) as response:
            for raw in response:
                if ms(started) > self.timeout * 1000:
                    raise TimeoutError("Chat response exceeded total deadline")
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                if adapter == "chat_sse":
                    if not line.startswith("data:"):
                        continue
                    line = line[5:].strip()
                    if line == "[DONE]":
                        completed = True
                        break
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                if adapter == "ollama":
                    token = event.get("message", {}).get("content", "")
                    if event.get("done"):
                        completed = True
                        for field in ("load_duration", "prompt_eval_duration", "eval_duration"):
                            if field in event:
                                metrics["server_" + field + "_ms"] = event[field] / 1e6
                        if event.get("eval_duration", 0) > 0:
                            metrics["server_tokens_per_s"] = event["eval_count"] / (event["eval_duration"] / 1e9)
                        if "eval_count" in event:
                            metrics["output_tokens"] = event["eval_count"]
                else:
                    choices = event.get("choices", [])
                    token = choices[0].get("delta", {}).get("content", "") if choices else ""
                    if choices and choices[0].get("finish_reason"):
                        completed = True
                    if event.get("usage", {}).get("completion_tokens") is not None:
                        metrics["output_tokens"] = event["usage"]["completion_tokens"]
                if token:
                    if first is None:
                        first = ms(started)
                    pieces.append(token)
        if not completed:
            raise RuntimeError("Chat stream ended without completion marker")
        text = "".join(pieces)
        if not text.strip():
            raise RuntimeError("Model returned no visible text")
        metrics["first_text_ms"] = first
        return {"text": text, "metrics": metrics}


class Worker(Provider):
    """One persistent process per provider: model load is outside warm measurements."""
    def start(self):
        command = [part.replace("{root}", str(self.root)).replace("{python}", sys.executable)
                   for part in self.config["command"]]
        self.log = self.log_path.open("w", encoding="utf-8")
        self.process = None
        try:
            self.process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, stderr=self.log, text=True,
                                            encoding="utf-8", bufsize=1,
                                            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.lines = queue.Queue()
            def collect():
                for line in self.process.stdout:
                    self.lines.put(line)
                self.lines.put(None)
            self.reader = threading.Thread(target=collect, daemon=True)
            self.reader.start()
            def expand(value):
                if isinstance(value, str):
                    return value.replace("{root}", str(self.root))
                if isinstance(value, dict):
                    return {k: expand(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [expand(v) for v in value]
                return value
            result = self.exchange({"op": "load", "config": expand(self.config)})
            self.metadata.update(result.get("metadata", {}))
            return self.metadata
        except BaseException:
            self.close()
            raise

    def exchange(self, request):
        if self.process.poll() is not None:
            raise RuntimeError("Worker exited; see worker log")
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        try:
            line = self.lines.get(timeout=self.timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError("Worker exceeded deadline and was stopped") from None
        if line is None:
            raise RuntimeError("Worker exited without a response; see worker log")
        result = json.loads(line)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def run(self, case, output):
        return self.exchange({"op": "run", "case": case, "output": str(output)})

    def close(self):
        process = getattr(self, "process", None)
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            reader = getattr(self, "reader", None)
            if reader:
                reader.join(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        if getattr(self, "log", None):
            self.log.close()


def create_provider(config, root, log):
    cls = {"mock": Mock, "worker": Worker}.get(config["adapter"], HTTP)
    return cls(config, root, log)
