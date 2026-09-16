import datetime as dt
import contextlib
import json
import platform
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import __version__
from .config import digest, load_config
from .metrics import recognition_errors, wav_info
from .providers import create_provider, ms


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def append_json(path, value):
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")


def hardware():
    data = {"platform": platform.platform(), "python": sys.version,
            "cpu": platform.processor(), "lab_version": __version__}
    try:
        command = ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.used",
                   "--format=csv,noheader"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data["gpu_snapshot"] = result.stdout.strip() if result.returncode == 0 else "unavailable"
    except (OSError, subprocess.TimeoutExpired):
        data["gpu_snapshot"] = "unavailable"
    return data


def scrub(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in {"api_key", "password", "token", "authorization"}
                    else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def stage_run(provider, kind, case, output):
    started = time.perf_counter()
    # Ground-truth transcripts are evaluation data, never recognizer input.
    request_case = {k: v for k, v in case.items() if k not in {"reference", "audio_sha256"}}
    result = provider.run(request_case, output)
    elapsed = ms(started)
    metrics = dict(result.get("metrics", {}))
    metrics["request_ms"] = elapsed
    if kind == "tts":
        if Path(result.get("audio_path", "")).resolve() != output.resolve():
            raise ValueError("TTS must write the requested output path")
        info = wav_info(output)
        metrics.update(info)
        metrics["rtf"] = elapsed / 1000 / info["audio_duration_s"]
        metrics["audio_file_ready_ms"] = elapsed
    elif not isinstance(result.get("text"), str):
        raise ValueError("Provider must return text")
    if kind == "stt":
        metrics.update(recognition_errors(case["reference"], result["text"]))
        duration = wav_info(case["audio"])["audio_duration_s"]
        metrics["input_audio_duration_s"] = duration
        metrics["rtf"] = elapsed / 1000 / duration
    result["metrics"] = metrics
    return result


def execute(exp, case, providers, folder, sample_id):
    mode = exp["mode"]
    stages, outputs = {}, {}
    synthetic = any(p.metadata.get("synthetic", False) for p in providers.values())
    started = time.perf_counter()
    if mode != "pipeline":
        result = stage_run(providers[mode], mode, case, folder / "audio" / f"{sample_id}.wav")
        synthetic = synthetic or result.get("synthetic", False)
        metrics = result["metrics"]
        outputs[mode] = {k: v for k, v in result.items() if k != "metrics"}
    else:
        current = dict(case)
        if "stt" in providers:
            stt = stage_run(providers["stt"], "stt", case, folder / "audio" / f"{sample_id}-stt.wav")
            stages["stt"] = stt["metrics"]
            synthetic = synthetic or stt.get("synthetic", False)
            current["text"] = stt["text"]
            outputs["stt"] = {"text": stt["text"]}
        llm = stage_run(providers["llm"], "llm", current, folder / "audio" / f"{sample_id}-llm.wav")
        stages["llm"] = llm["metrics"]
        synthetic = synthetic or llm.get("synthetic", False)
        outputs["llm"] = {"text": llm["text"]}
        tts = stage_run(providers["tts"], "tts", {**case, "text": llm["text"]},
                        folder / "audio" / f"{sample_id}.wav")
        stages["tts"] = tts["metrics"]
        synthetic = synthetic or tts.get("synthetic", False)
        outputs["tts"] = {"audio_path": tts["audio_path"]}
        metrics = {"pipeline_total_ms": ms(started)}
        metrics["audio_file_ready_ms"] = metrics["pipeline_total_ms"]
        for stage, values in stages.items():
            metrics.update({stage + "." + key: value for key, value in values.items()})
    for out in outputs.values():
        if out.get("audio_path"):
            path = Path(out["audio_path"])
            out["audio_sha256"] = digest(path)
            out["audio_path"] = path.relative_to(folder).as_posix()
    return {"metrics": metrics, "outputs": outputs, "synthetic": synthetic}


def run(config_path, output_root, selected=None):
    cfg, root = load_config(config_path)
    experiments = cfg["experiments"]
    if selected:
        known = {e["id"] for e in experiments}
        unknown = set(selected) - known
        if unknown:
            raise ValueError(f"Unknown experiments: {sorted(unknown)}")
        experiments = [e for e in experiments if e["id"] in selected]
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    folder = Path(output_root).resolve() / run_id
    (folder / "audio").mkdir(parents=True)
    (folder / "logs").mkdir()
    manifest = {"schema_version": 1, "id": run_id, "status": "running",
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "config": scrub(cfg), "selected": [e["id"] for e in experiments],
                "hardware_start": hardware(), "provider_sessions": [],
                "measurement": "serial full-file pipeline; no microphone endpointing or speaker playback",
                "source_sha256": {str(p.relative_to(Path(__file__).parent)): digest(p)
                                  for p in Path(__file__).parent.rglob("*.py")}}
    worker_script = root / "scripts" / "model_worker.py"
    if worker_script.is_file():
        manifest["source_sha256"]["scripts/model_worker.py"] = digest(worker_script)
    write_json(folder / "manifest.json", manifest)
    failed = 0
    active = []
    try:
        for exp in experiments:
            print(f"[{exp['id']}] loading", flush=True)
            stages = [exp["mode"]] if exp["mode"] != "pipeline" else [s for s in ("stt", "llm", "tts") if s in exp]
            providers = {}
            synthetic = any(cfg["providers"][exp[s]]["adapter"] == "mock" for s in stages)
            setup_error = None
            try:
                for stage in stages:
                    name = exp[stage]
                    p = create_provider(cfg["providers"][name], root, folder / "logs" / f"{exp['id']}-{stage}.log")
                    providers[stage] = p
                    active.append(p)
                    started = time.perf_counter()
                    metadata = p.start()
                    synthetic = synthetic or metadata.get("synthetic", False)
                    manifest["provider_sessions"].append({"experiment": exp["id"], "provider": name,
                                                          "setup_ms": ms(started), "metadata": metadata})
                for index in range(exp["warmups"]):
                    warm_id = f"warmup-{exp['id']}-{index}"
                    result = execute(exp, exp["cases"][0], providers, folder, warm_id)
                    append_json(folder / "warmups.jsonl", {"experiment": exp["id"], "index": index, **result})
            except Exception as exc:
                setup_error = f"{type(exc).__name__}: {exc}"
                print(f"  setup failed: {setup_error}", flush=True)
            try:
                jobs = [(case, repeat) for repeat in range(exp["repetitions"]) for case in exp["cases"]]
                random.Random(cfg.get("seed", 42)).shuffle(jobs)
                for case, repeat in jobs:
                    sample_id = uuid.uuid4().hex
                    record = {"sample_id": sample_id, "experiment": exp["id"], "mode": exp["mode"],
                              "case_id": case["id"], "category": case.get("category", "general"),
                              "repeat": repeat, "synthetic": synthetic,
                              "phase": "warm" if exp["warmups"] else "unwarmed",
                              "input": case, "status": "ok"}
                    try:
                        if setup_error:
                            raise RuntimeError(setup_error)
                        record.update(execute(exp, case, providers, folder, sample_id))
                    except Exception as exc:
                        failed += 1
                        record.update(status="error", error=f"{type(exc).__name__}: {exc}", metrics={})
                    append_json(folder / "samples.jsonl", record)
                print(f"  {len(jobs)} samples recorded", flush=True)
            finally:
                for p in reversed(list(providers.values())):
                    p.close()
                active.clear()
            write_json(folder / "manifest.json", manifest)
        manifest["status"] = "completed_with_errors" if failed else "completed"
    except BaseException:
        manifest["status"] = "interrupted"
        raise
    finally:
        for p in reversed(active):
            with contextlib.suppress(Exception):
                p.close()
        manifest["hardware_end"] = hardware()
        write_json(folder / "manifest.json", manifest)
    from .report import build_report
    build_report(folder)
    return folder, failed
