import argparse
import json
from pathlib import Path

from .config import load_config
from .report import build_report
from .runner import hardware, run
from .server import make_server
from .playground import make_playground


def main():
    parser = argparse.ArgumentParser(description="로컬 모델 플레이그라운드")
    sub = parser.add_subparsers(dest="command", required=True)
    play = sub.add_parser("play", help="모델을 선택하고 대화·음성을 바로 테스트")
    play.add_argument("--config", default="configs/local.json")
    play.add_argument("--port", type=int, default=8766)
    sub.add_parser("doctor", help="현재 장비 정보 출력; 모델 설치 여부는 추론하지 않음")
    check = sub.add_parser("validate", help="설정과 데이터셋 검증")
    check.add_argument("config")
    bench = sub.add_parser("run", help="벤치마크 실행")
    bench.add_argument("config")
    bench.add_argument("--output", default="runs")
    bench.add_argument("--only", nargs="+")
    report = sub.add_parser("report", help="CSV/JSON/Markdown 보고서 다시 생성")
    report.add_argument("folder")
    serve = sub.add_parser("serve", help="로컬 비교 및 블라인드 청취 화면")
    serve.add_argument("folder")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "play":
            server = make_playground(args.config, args.port)
            print(f"Open http://127.0.0.1:{server.server_port} (Ctrl+C to stop)", flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        elif args.command == "doctor":
            print(json.dumps(hardware(), ensure_ascii=False, indent=2))
        elif args.command == "validate":
            cfg, _ = load_config(args.config)
            print(f"Valid: {len(cfg['providers'])} providers, {len(cfg['experiments'])} experiments")
        elif args.command == "run":
            folder, failures = run(args.config, args.output, args.only)
            print(f"Results: {folder}\nFailed samples: {failures}")
            return 1 if failures else 0
        elif args.command == "report":
            if not (Path(args.folder) / "manifest.json").is_file():
                raise ValueError("Run folder must contain manifest.json")
            build_report(args.folder)
            print(Path(args.folder).resolve() / "report.md")
        else:
            if not (Path(args.folder) / "manifest.json").is_file():
                raise ValueError("Run folder must contain manifest.json")
            server = make_server(args.folder, args.port)
            print(f"Open http://127.0.0.1:{server.server_port} (Ctrl+C to stop)", flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        return 0
    except KeyboardInterrupt:
        print("Stopped. Existing results are retained.")
        return 130
    except (OSError, ValueError, KeyError) as exc:
        print(f"Error: {exc}")
        return 2
