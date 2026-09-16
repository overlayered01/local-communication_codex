import datetime as dt
import json
import random
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .report import build_report, read_lines
from .runner import append_json

DIMENSIONS = {"naturalness", "pronunciation", "prosody", "content"}


def make_server(folder, port=8765):
    folder = Path(folder).resolve()
    samples = read_lines(folder / "samples.jsonl")
    good = [s for s in samples if s["status"] == "ok"]
    random.SystemRandom().shuffle(good)
    # Opaque session ids keep engine names and file paths out of the listening UI.
    mapping = {uuid.uuid4().hex: s for s in good}
    lock = threading.Lock()
    page = Path(__file__).with_name("web").joinpath("index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host not in allowed:
                self.send_error(403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in allowed}:
                self.send_error(403)
                return False
            return True

        def respond(self, data, content_type="application/json; charset=utf-8", status=200):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return
            route = urlparse(self.path).path
            if route == "/":
                return self.respond(page, "text/html; charset=utf-8")
            if route == "/api/listen":
                items = []
                for index, (token, sample) in enumerate(mapping.items(), 1):
                    outputs = sample["outputs"]
                    text = outputs.get("llm", outputs.get("stt", {})).get("text", "")
                    items.append({"id": token, "label": f"샘플 {index:03}",
                                  "case": sample["case_id"], "category": sample["category"],
                                  "input": sample["input"].get("text", sample["input"].get("reference", "")),
                                  "text": text, "audio": "tts" in outputs,
                                  "synthetic": sample["synthetic"]})
                return self.respond(items)
            if route == "/api/summary":
                with lock:
                    build_report(folder)
                    value = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
                return self.respond(value)
            if route.startswith("/audio/"):
                sample = mapping.get(route.rsplit("/", 1)[-1])
                relative = sample.get("outputs", {}).get("tts", {}).get("audio_path") if sample else None
                if relative:
                    path = (folder / relative).resolve()
                    if path.is_relative_to(folder / "audio") and path.is_file():
                        return self.respond(path.read_bytes(), "audio/wav")
            self.send_error(404)

        def do_POST(self):
            if not self.allowed():
                return
            if self.path != "/api/review":
                return self.send_error(404)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 16384 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Expected a small JSON request")
                data = json.loads(self.rfile.read(size))
                sample = mapping[data["id"]]
                if sample["synthetic"]:
                    raise ValueError("Demo samples cannot be scored as model quality")
                scores = data["scores"]
                if not scores or not set(scores) <= DIMENSIONS:
                    raise ValueError("Invalid rating dimensions")
                if any(type(v) is not int or not 1 <= v <= 5 for v in scores.values()):
                    raise ValueError("Scores must be integers from 1 to 5")
                reviewer = data.get("reviewer", "").strip()
                if not reviewer or len(reviewer) > 80:
                    raise ValueError("Reviewer id required (1–80 characters)")
                notes = data.get("notes", "")
                if not isinstance(notes, str) or len(notes) > 2000:
                    raise ValueError("Notes must be text up to 2000 characters")
                record = {"sample_id": sample["sample_id"], "reviewer": reviewer,
                          "scores": scores, "notes": notes,
                          "created_at": dt.datetime.now(dt.timezone.utc).isoformat()}
                with lock:
                    append_json(folder / "reviews.jsonl", record)
                self.respond({"saved": True})
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                self.respond({"error": str(exc)}, status=400)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
