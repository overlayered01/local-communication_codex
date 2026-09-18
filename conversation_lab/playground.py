"""Interactive model playground, independent of benchmark datasets and runs."""
import copy
import base64
import binascii
import io
import hashlib
import json
import math
import re
import threading
import time
import urllib.request
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .config import load_config
from .providers import create_provider
from .runner import stage_run
from .options import schema, apply_options, public_settings
from .library import Library, rag_prompt


class Playground:
    def __init__(self, config_path):
        self.config, self.root = load_config(config_path, validate_experiments=False)
        self.folder = self.root / "runs" / "playground" / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.models = {}
        self.jobs = {}
        self.lock = threading.RLock()
        self.busy = threading.Lock()
        self.worker = None
        self.worker_config = None
        self.library = Library(self.root)

    def discover(self):
        models, connections, embedders = {}, [], {}
        endpoints = {}
        for name, original in self.config["providers"].items():
            cfg = copy.deepcopy(original)
            if cfg["adapter"] in {"ollama", "chat_sse"}:
                endpoints.setdefault((cfg["adapter"], cfg["url"]), cfg)
                continue
            if cfg["kind"] not in {"tts", "stt"} or cfg["adapter"] == "mock":
                continue
            ready, reason = True, "설정됨 · 실행 시 연결 확인"
            if cfg["adapter"] == "worker":
                exe = Path(cfg["command"][0].replace("{root}", str(self.root)))
                model = cfg.get("load", {}).get("model_dir", cfg.get("model", ""))
                path = Path(model.replace("{root}", str(self.root)))
                ready = exe.is_file() and path.exists()
                reason = "설치됨" if ready else "실행 환경 또는 모델 설치 필요"
            models[name] = {"id": name, "label": name, "kind": cfg["kind"], "ready": ready,
                            "reason": reason, "engine": cfg.get("engine", cfg["adapter"]), "config": cfg}
        for (adapter, endpoint), base in endpoints.items():
            list_url = endpoint.rsplit("/", 1)[0] + "/tags" if adapter == "ollama" else endpoint.rsplit("/chat/completions", 1)[0] + "/models"
            try:
                headers = {}
                if base.get("api_key_env"):
                    import os
                    token = os.environ.get(base["api_key_env"])
                    if not token:
                        raise ValueError("설정한 인증 환경 변수가 없습니다")
                    headers["Authorization"] = "Bearer " + token
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(urllib.request.Request(list_url, headers=headers), timeout=3) as response:
                    result = json.load(response)
                names = [m["name"] for m in result["models"]] if adapter == "ollama" else [m["id"] for m in result["data"]]
                def embedding_model(name):
                    return 'embed' in name.lower() or name.lower().split(':')[0].split('/')[-1] == 'bge-m3'
                for name in names:
                    if embedding_model(name):
                        key = 'embed-' + hashlib.sha256((endpoint+name).encode()).hexdigest()[:16]
                        embed_url = endpoint.rsplit('/',1)[0]+'/embed' if adapter == 'ollama' else endpoint.rsplit('/chat/completions',1)[0]+'/embeddings'
                        embedders[key] = dict(adapter=adapter,url=embed_url,model=name,api_key_env=base.get('api_key_env'))
                # Model-list endpoints may include embedding-only models, which cannot chat.
                names = [name for name in names if not embedding_model(name)]
                for name in names:
                    cfg = copy.deepcopy(base)
                    cfg.update(model=name, unload_after=False, timeout_s=300)
                    if adapter == "ollama":
                        cfg["params"] = {"keep_alive": "5m", "options": {"num_ctx": 4096, "num_predict": 512}}
                        if name.startswith("qwen3"):
                            cfg["params"]["think"] = False
                    key = "llm-" + hashlib.sha256((endpoint + name).encode()).hexdigest()[:16]
                    models[key] = {"id": key, "label": name, "kind": "llm", "ready": True,
                                   "reason": "서버에서 발견됨", "engine": adapter, "config": cfg}
                connections.append({"engine": adapter, "connected": True, "count": len(names)})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                connections.append({"engine": adapter, "connected": False, "message": "서버 연결 또는 모델 목록 확인 실패"})
        with self.lock:
            self.models = models
        with self.library.lock:
            self.library.embedders = embedders
        for model in models.values():
            model['options'] = schema(model['config'])
        return {"models": [{k: v for k, v in m.items() if k != "config"} for m in models.values()],
                "connections": connections}

    def submit(self, data):
        if not isinstance(data, dict):
            raise ValueError("요청 형식이 올바르지 않습니다")
        mode, text = data.get("mode"), data.get("text", "")
        selected = data.get('stages')
        if selected is not None:
            if not isinstance(selected, dict) or set(selected) != {'stt','llm','tts'} or any(type(v) is not bool for v in selected.values()) or not any(selected.values()):
                raise ValueError('STT·LLM·TTS 중 실행할 단계를 하나 이상 선택하세요')
            stages = [s for s in ('stt','llm','tts') if selected[s]]
        else:
            if mode not in {'chat','tts','pipeline','voice'}:
                raise ValueError('지원하지 않는 모드입니다')
            stages = ["stt", "llm", "tts"] if mode == "voice" else ["llm", "tts"] if mode == "pipeline" else ["llm" if mode == "chat" else "tts"]
        if not isinstance(text, str) or ('stt' not in stages and not 0 < len(text.strip()) <= 8000):
            raise ValueError("모드와 1~8,000자의 입력이 필요합니다")
        audio_bytes = None
        if 'stt' in stages:
            try:
                encoded = data.get("audio_base64", "")
                if not isinstance(encoded, str) or len(encoded) > 8_000_000:
                    raise ValueError("녹음은 최대 60초입니다")
                audio_bytes = base64.b64decode(encoded, validate=True)
                with wave.open(io.BytesIO(audio_bytes)) as recording:
                    if recording.getnchannels() != 1 or recording.getsampwidth() != 2 or not 8000 <= recording.getframerate() <= 48000:
                        raise ValueError("모노 16비트 PCM WAV 녹음이 필요합니다")
                    if not 0.1 <= recording.getnframes() / recording.getframerate() <= 60:
                        raise ValueError("녹음 길이는 0.1~60초여야 합니다")
                    if len(recording.readframes(recording.getnframes())) != recording.getnframes() * 2:
                        raise ValueError("녹음 데이터가 불완전합니다")
            except (binascii.Error, wave.Error, EOFError) as exc:
                raise ValueError("녹음 WAV 데이터를 읽을 수 없습니다") from exc
        messages = data.get("messages", [])
        if not isinstance(messages, list) or len(messages) > 40:
            raise ValueError("대화 이력은 최대 40개 메시지까지 전달할 수 있습니다")
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in {"user", "assistant"} or not isinstance(m.get("content"), str) or len(m["content"]) > 8000:
                raise ValueError("대화 이력 형식이 올바르지 않습니다")
        configs = {}
        with self.lock:
            for stage in stages:
                entry = self.models.get(data.get(stage))
                if not entry or entry["kind"] != stage or not entry["ready"]:
                    raise ValueError("사용할 수 있는 모델을 선택해 주세요")
                configs[stage] = copy.deepcopy(entry["config"])
        if "llm" in configs:
            temperature = data.get("temperature", 0.7)
            if type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2:
                raise ValueError("Temperature는 0~2 범위여야 합니다")
            system = data.get("system", "한국어로 자연스럽고 간결하게 답하세요.")
            if not isinstance(system, str) or len(system) > 4000:
                raise ValueError("시스템 지침은 4,000자 이내여야 합니다")
            cfg = configs["llm"]
            cfg["system"] = system
            params = cfg.setdefault("params", {})
            if cfg["adapter"] == "ollama":
                params.setdefault("options", {})["temperature"] = temperature
            else:
                params["temperature"] = temperature
        if "tts" in configs and configs["tts"].get("engine") == "supertonic":
            cfg = configs["tts"]
            voice = data.get("voice", cfg.get("voice", "F1"))
            speed = data.get("speed", 1)
            if voice not in [f"{g}{i}" for g in "FM" for i in range(1, 6)]:
                raise ValueError("지원하지 않는 목소리입니다")
            if type(speed) not in (int, float) or not math.isfinite(speed) or not 0.7 <= speed <= 2:
                raise ValueError("속도는 0.7~2 범위여야 합니다")
            cfg["voice"] = voice
            cfg["params"]["speed"] = speed
        if "tts" in configs and configs["tts"].get("engine") == "qwen_tts":
            cfg = configs["tts"]
            speaker = data.get("speaker", "Sohee")
            if speaker not in {"Sohee", "Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna"}:
                raise ValueError("지원하지 않는 Qwen 목소리입니다")
            cfg["params"]["speaker"] = speaker
            cfg["params"].setdefault("max_new_tokens", 1024)
            if "llm" in configs and configs["llm"]["adapter"] == "ollama":
                # Release this request's LLM before loading a GPU TTS model.
                configs["llm"]["params"]["keep_alive"] = 0
        options = data.get('options',{})
        if not isinstance(options,dict) or options.keys()-{'llm','tts','stt'}:
            raise ValueError('옵션 구성에 알 수 없는 항목이 있습니다')
        for stage in configs:
            configs[stage] = apply_options(configs[stage],options.get(stage,{}))
        rag = self.library.validate_rag(data.get('rag',{})) if 'llm' in configs else {'enabled':False}
        if not self.busy.acquire(blocking=False):
            raise BlockingIOError("다른 요청을 실행 중입니다. 완료 후 다시 시도하세요.")
        job_id = uuid.uuid4().hex
        with self.lock:
            if len(self.jobs) >= 100:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[job_id] = {"id": job_id, "status": "running", "phase": "모델 준비 중"}
        threading.Thread(target=self._run, args=(job_id, text, messages, configs, audio_bytes, rag), daemon=True).start()
        return {"id": job_id}

    def _run(self, job_id, text, messages, configs, audio_bytes=None, rag=None):
        started = time.perf_counter()
        result = {"text": "", "timings": {}, 'settings':public_settings(configs), 'rag':rag or {'enabled':False}, 'sources':[]}
        result['stages'] = {s:{'status':'queued' if s in configs else 'skipped'} for s in ('stt','llm','tts')}
        stage = None
        try:
            if audio_bytes is not None:
                (self.folder / (job_id + "-input.wav")).write_bytes(audio_bytes)
            if "llm" in configs and self.worker_config and self.worker_config.get("engine") == "qwen_tts":
                self.worker.close()
                self.worker, self.worker_config = None, None
            for stage, cfg in configs.items():
                trace = result['stages'][stage]
                trace.update(status='running', input='녹음 WAV' if stage == 'stt' else text)
                with self.lock:
                    self.jobs[job_id].update(copy.deepcopy(result))
                if stage == 'llm' and rag and rag['enabled']:
                    with self.lock:
                        self.jobs[job_id]['phase'] = '문서에서 관련 내용을 찾는 중'
                    result['sources'] = self.library.search(text,rag)
                    cfg['system'] = rag_prompt(cfg.get('system',''),result['sources'])
                    result['settings'] = public_settings(configs)
                with self.lock:
                    self.jobs[job_id]["phase"] = {"stt": "말씀을 인식하는 중", "llm": "답변 생성 중", "tts": "음성 준비·생성 중"}[stage]
                provider = None
                try:
                    if cfg["adapter"] == "worker":
                        if cfg != self.worker_config:
                            if self.worker:
                                self.worker.close()
                            self.worker, self.worker_config = None, None
                            provider = create_provider(cfg, self.root, self.folder / (job_id + ".log"))
                            provider.start()
                            self.worker, self.worker_config = provider, cfg
                        provider = self.worker
                    else:
                        provider = create_provider(cfg, self.root, self.folder / (job_id + ".log"))
                        provider.start()
                    output = self.folder / (job_id + ".wav")
                    if stage == "stt":
                        recognition_started = time.perf_counter()
                        value = provider.run({"audio": str(self.folder / (job_id + "-input.wav"))}, output)
                        text = value.get("text", "").strip()
                        if not text:
                            raise ValueError("말소리를 인식하지 못했습니다. 마이크를 확인하고 다시 말씀해 주세요.")
                        value["metrics"] = {"request_ms": (time.perf_counter() - recognition_started) * 1000}
                        result["transcript"] = text
                    else:
                        input_text = re.sub(r'\[\d+\]','',text) if stage == 'tts' and rag and rag['enabled'] else text
                        trace['input'] = input_text
                        value = stage_run(provider, stage, {"text": input_text, "messages": messages}, output)
                    result["timings"][stage] = value["metrics"]["request_ms"]
                    if stage == "llm":
                        text = value["text"]
                        result["text"] = text
                    elif stage == "tts":
                        result["audio"] = "/audio/" + job_id
                    trace.update(status='done', output=result.get('audio') if stage == 'tts' else text, elapsed_ms=result['timings'][stage])
                    result['text'] = text
                    with self.lock:
                        self.jobs[job_id].update(copy.deepcopy(result))
                finally:
                    if provider and provider is not self.worker:
                        provider.close()
            result["elapsed_ms"] = (time.perf_counter() - started) * 1000
            with self.lock:
                self.jobs[job_id].update(status="done", phase="완료", **result)
        except Exception as exc:
            if stage:
                result['stages'][stage].update(status='error', error=str(exc))
            for trace in result['stages'].values():
                if trace['status'] == 'queued':
                    trace['status'] = 'blocked'
            if self.worker:
                self.worker.close()
                self.worker, self.worker_config = None, None
            with self.lock:
                self.jobs[job_id].update(status="error", error=str(exc), **result)
        finally:
            self.busy.release()

    def close(self):
        with self.busy:
            if self.worker:
                self.worker.close()
        self.library.close()


def make_playground(config_path, port=8766):
    app = Playground(config_path)
    page_path = Path(__file__).with_name("web").joinpath("playground.html")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed(self):
            hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in hosts or self.headers.get("Origin") not in {None, *("http://" + h for h in hosts)}:
                self.send_error(403)
                return False
            return True

        def respond(self, data, status=200, content_type="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
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
            path = urlparse(self.path).path
            if path == "/":
                return self.respond(page_path.read_bytes(), content_type="text/html; charset=utf-8")
            if path == "/playground.js":
                return self.respond(Path(__file__).with_name("web").joinpath("playground.js").read_bytes(), content_type="text/javascript; charset=utf-8")
            if path == '/studio.js':
                return self.respond(Path(__file__).with_name('web').joinpath('studio.js').read_bytes(),content_type='text/javascript; charset=utf-8')
            if path == "/api/models":
                return self.respond(app.discover())
            if path == '/api/library':
                return self.respond(app.library.state())
            if path.startswith("/api/jobs/") or path.startswith("/audio/"):
                key = path.rsplit("/", 1)[-1]
                with app.lock:
                    job = copy.deepcopy(app.jobs.get(key))
                if job:
                    if path.startswith("/api/jobs/"):
                        return self.respond(job)
                    if job.get("audio"):
                        return self.respond((app.folder / (key + ".wav")).read_bytes(), content_type="audio/wav")
            self.send_error(404)

        def do_POST(self):
            if not self.allowed():
                return
            if self.path != "/api/generate" and not self.path.startswith('/api/library/'):
                return self.send_error(404)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8_400_000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("JSON 요청이 필요합니다 (녹음 최대 60초)")
                data = json.loads(self.rfile.read(size))
                if self.path == '/api/generate':
                    return self.respond(app.submit(data),202)
                action = self.path.rsplit('/',1)[-1]
                if action == 'search':
                    query=data.get('query','')
                    if not isinstance(query,str) or not 0<len(query.strip())<=8000:
                        raise ValueError('검색할 질문을 입력해 주세요')
                    return self.respond({'sources':app.library.search(query,data.get('rag',{}))})
                return self.respond(app.library.mutate(action,data))
            except BlockingIOError as exc:
                return self.respond({"error": str(exc)}, 409)
            except (ValueError, TypeError, KeyError) as exc:
                return self.respond({"error": str(exc)}, 400)
            except Exception as exc:
                return self.respond({'error': '작업을 완료하지 못했습니다: '+str(exc)}, 500)

    class Server(ThreadingHTTPServer):
        def server_close(self):
            super().server_close()
            app.close()

    server = Server(("127.0.0.1", port), Handler)
    server.app = app
    return server
