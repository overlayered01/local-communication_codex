import csv
import json
from collections import defaultdict
from pathlib import Path

from .metrics import percentile
from .runner import write_json


def read_lines(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def summarize(samples):
    groups = defaultdict(list)
    for sample in samples:
        groups[(sample["experiment"], sample["phase"], sample["synthetic"])].append(sample)
    results = []
    for (experiment, phase, synthetic), rows in groups.items():
        good = [r for r in rows if r["status"] == "ok"]
        values = defaultdict(list)
        for row in good:
            for metric, value in row["metrics"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values[metric].append(value)
        metrics = {k: {"n": len(v), "median": percentile(v, 50), "p95": percentile(v, 95),
                       "min": min(v), "max": max(v)} for k, v in values.items()}
        # Corpus errors weight each reference by its length, rather than averaging rates.
        for prefix in ("", "stt."):
            for label, edits, denominator in (("cer", "char_edits", "reference_chars"),
                                               ("wer", "word_edits", "reference_words")):
                if prefix + denominator in values:
                    metrics[prefix + "corpus_" + label] = sum(values[prefix + edits]) / sum(values[prefix + denominator])
        results.append({"experiment": experiment, "phase": phase, "synthetic": synthetic,
                        "attempted": len(rows), "succeeded": len(good),
                        "failure_rate": 1 - len(good) / len(rows), "metrics": metrics})
    return results


def build_report(folder):
    folder = Path(folder)
    samples = read_lines(folder / "samples.jsonl")
    summary = summarize(samples)
    reviews = read_lines(folder / "reviews.jsonl")
    # Keep the latest rating by one reviewer for a sample, preserving the audit log.
    latest = {(r["sample_id"], r["reviewer"]): r for r in reviews}
    sample_map = {s["sample_id"]: s for s in samples}
    ratings = defaultdict(lambda: defaultdict(list))
    for review in latest.values():
        sample = sample_map.get(review["sample_id"])
        if sample and sample["status"] == "ok" and not sample["synthetic"]:
            for dimension, value in review["scores"].items():
                ratings[sample["experiment"]][dimension].append(value)
    quality = {exp: {dim: {"n": len(values), "mean": sum(values) / len(values)}
                     for dim, values in dimensions.items()} for exp, dimensions in ratings.items()}
    write_json(folder / "summary.json", {"experiments": summary, "human_ratings": quality})
    with (folder / "samples.csv").open("w", encoding="utf-8-sig", newline="") as f:
        metric_names = sorted({k for s in samples for k in s.get("metrics", {})})
        columns = ["sample_id", "experiment", "case_id", "category", "repeat", "phase", "synthetic", "status", "error"]
        writer = csv.DictWriter(f, fieldnames=columns + metric_names)
        writer.writeheader()
        for sample in samples:
            writer.writerow({**{k: sample.get(k, "") for k in columns}, **sample.get("metrics", {})})
    lines = ["# llm-communication_codex — 비교 결과", "",
             "합성 데모 결과는 모델 성능 근거가 아닙니다. 실패한 요청은 지연 통계에서 제외하며 실패율을 별도로 표시합니다.", "",
             "| 실험 | 상태 | 성공/전체 | 실패율 | 요청/파이프라인 중앙값(ms) | p95(ms) |", "|---|---|---:|---:|---:|---:|"]
    for item in summary:
        timing = item["metrics"].get("pipeline_total_ms", item["metrics"].get("request_ms", {}))
        def fmt(v):
            return "—" if v is None else f"{v:.2f}"
        lines.append(f"| {item['experiment']} | {'DEMO / ' if item['synthetic'] else ''}{item['phase']} | "
                     f"{item['succeeded']}/{item['attempted']} | {item['failure_rate']:.1%} | "
                     f"{fmt(timing.get('median'))} | {fmt(timing.get('p95'))} |")
    lines += ["", "## 해석 범위", "",
              "- first_text_ms: 요청부터 첫 표시용 텍스트 수신까지. 추론용 thinking은 제외.",
              "- audio_file_ready_ms: 전체 WAV 파일 완성까지. 첫 오디오 청크 또는 실제 스피커 재생 지연이 아님.",
              "- pipeline_total_ms: 입력 텍스트/완성된 녹음에서 시작하는 순차 실행. 음성 종료 감지·실시간 전송·끼어들기는 미측정.",
              "- RTF: 요청 소요 시간 / 음성 길이. 1 미만이면 생성/인식이 해당 음성 재생보다 빠름.",
              "- p95는 선형 보간. 적은 표본이나 같은 문장 반복만으로 운영 환경의 p95를 확정하지 않음.",
              "- STT CER/WER: NFC·소문자·문장부호 제거 후 계산. CER은 공백 제외, WER은 공백 단위.",
              "- 설정·데이터 해시·프로세스 버전·GPU 시작/종료 스냅샷은 manifest.json. 최대 VRAM은 미측정.",
              "- 실패/중단 결과와 synthetic 결과를 확인한 후 판단. 서로 다른 데이터셋·조건은 직접 순위화하지 않음.", "",
              "## 청취 평가", "", "자연스러움은 사람이 평가합니다. 음질 우승 모델을 자동으로 추정하지 않습니다.", "",
              "```json", json.dumps(quality, ensure_ascii=False, indent=2), "```", ""]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return summary
