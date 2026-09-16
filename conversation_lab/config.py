import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

KINDS = {"llm", "tts", "stt"}
ADAPTERS = {"mock", "ollama", "chat_sse", "tts_http", "worker"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_config(path):
    path = Path(path).resolve()
    cfg = read_json(path)
    if cfg.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    root = (path.parent / cfg.get("root", ".")).resolve()
    providers = cfg.get("providers", {})
    if not providers or not cfg.get("experiments"):
        raise ValueError("providers and experiments are required")
    ids = set()
    for name, provider in providers.items():
        if provider.get("kind") not in KINDS or provider.get("adapter") not in ADAPTERS:
            raise ValueError(f"Invalid provider: {name}")
        adapter, kind = provider["adapter"], provider["kind"]
        if adapter in {"ollama", "chat_sse"} and kind != "llm":
            raise ValueError(f"{name}: chat adapter requires llm kind")
        if adapter == "tts_http" and kind != "tts":
            raise ValueError(f"{name}: tts_http requires tts kind")
        if adapter == "mock" and kind == "stt":
            raise ValueError("Mock STT is not supported; use a real recording and recognizer")
        if adapter == "worker" and (not isinstance(provider.get("command"), list) or not provider["command"]):
            raise ValueError(f"{name}: command must be a non-empty argv array")
        if adapter in {"ollama", "chat_sse", "tts_http"}:
            url = urlparse(provider.get("url", ""))
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise ValueError(f"{name}: invalid endpoint URL")
            if url.hostname not in {"localhost", "127.0.0.1", "::1"} and not cfg.get("allow_remote", False):
                raise ValueError(f"{name}: remote endpoints require allow_remote=true")
        if provider.get("timeout_s", 120) <= 0:
            raise ValueError("timeout_s must be positive")
    for exp in cfg["experiments"]:
        eid = exp.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", eid) or eid in ids:
            raise ValueError(f"Invalid or duplicate experiment id: {eid}")
        ids.add(eid)
        mode = exp.get("mode")
        if mode not in KINDS | {"pipeline"}:
            raise ValueError(f"Unknown mode: {mode}")
        required = [mode] if mode != "pipeline" else ["llm", "tts"]
        if mode == "pipeline" and "stt" in exp:
            required.insert(0, "stt")
        for stage in required:
            name = exp.get(stage)
            if name not in providers or providers[name]["kind"] != stage:
                raise ValueError(f"{eid}: missing or wrong provider for {stage}")
        for key, minimum in (("repetitions", 1), ("warmups", 0)):
            value = exp.get(key, cfg.get(key, 3 if key == "repetitions" else 1))
            if type(value) is not int or value < minimum:
                raise ValueError(f"{key} must be an integer >= {minimum}")
            exp[key] = value
        dataset = (root / exp["dataset"]).resolve()
        cases = []
        seen = set()
        for line in dataset.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            cid = case.get("id")
            if not isinstance(cid, str) or not cid or cid in seen:
                raise ValueError(f"{eid}: invalid or duplicate case id")
            seen.add(cid)
            if "stt" in required:
                audio = (dataset.parent / case["audio"]).resolve()
                if not audio.is_file() or not case.get("reference", "").strip():
                    raise ValueError(f"{cid}: audio file and reference required")
                case["audio"] = str(audio)
                case["audio_sha256"] = digest(audio)
            elif not isinstance(case.get("text"), str) or not case["text"].strip():
                raise ValueError(f"{cid}: text required")
            cases.append(case)
        if not cases:
            raise ValueError(f"{eid}: dataset is empty")
        exp["cases"] = cases
        exp["dataset_sha256"] = digest(dataset)
    return cfg, root
