# llm-communication_codex

로컬 **STT·LLM·TTS와 그 조합을 비교하고, 실제 대화 제품에 사용할 구성을 선정하는 실험 프로젝트**입니다.

특정 모델에 고정하지 않습니다. 같은 평가 데이터로 설정을 바꿔 실행하고, 속도·실패·출력·사람의 평가를 함께 남깁니다.

## 바로 실행

Python 3.11 이상. 평가 도구 자체에는 외부 패키지가 필요 없습니다. 저장소 루트에서:

```powershell
python -m conversation_lab validate configs/demo.json
python -m conversation_lab run configs/demo.json
# 출력된 실제 실행 폴더를 넣으세요.
python -m conversation_lab serve runs/<실행-ID>
```

브라우저에서 `http://127.0.0.1:8765`를 열면 블라인드 평가와 측정 결과를 볼 수 있습니다.
**demo는 가짜 응답과 사인파로 실행 흐름만 확인합니다. 실제 음성·모델 품질이나 성능 증거가 아닙니다.** 모의 샘플에는 품질 점수를 저장할 수 없습니다.

```powershell
python -m unittest discover -s tests -v
python -m conversation_lab doctor
python -m conversation_lab report runs/<실행-ID>
```

## 비교 범위

| 영역 | 현재 구현 | 설정 예시 |
|---|---|---|
| LLM | Ollama NDJSON, 호환 Chat SSE 서버 | Qwen3.5 4B·9B, LM Studio 연결 설정 |
| TTS | 별도 프로세스에서 Supertonic·Qwen CustomVoice 실행, WAV HTTP 서버 | Supertonic 3의 5·8·12 steps, Qwen 0.6B·1.7B |
| STT | faster-whisper 프로세스, 정답 전사와 CER/WER | small CPU int8 템플릿 |
| 조합 | LLM → TTS, 녹음 파일 → STT → LLM → TTS | 같은 구성요소를 단독/조합으로 비교 |
| 평가 | 지연 중앙값·p95·RTF·실패율·블라인드 평점 | 자연스러움, 발음, 억양, 내용 |

여기서 ‘구현’은 **연결 코드가 있음**을 의미합니다. 모든 외부 엔진의 설치·실행·성능이 이 PC에서 검증됐다는 뜻이 아닙니다. 실제 모델을 내려받고 실행한 결과만 모델 선정에 사용합니다.

## 실제 모델 연결

[설치 안내](docs/SETUP.md)에 따라 필요한 엔진만 준비합니다. 서로 다른 가상환경을 써서 라이브러리 충돌을 피합니다.

```powershell
python -m conversation_lab validate configs/local.json
python -m conversation_lab run configs/local.json --only supertonic-5 supertonic-8 supertonic-12
python -m conversation_lab run configs/local.json --only qwen-06 qwen-17
python -m conversation_lab run configs/local.json --only pipeline-4b-supertonic-8 pipeline-4b-qwen-17
```

한 실행 폴더에서 직접 청취 비교하려면 후보들을 **같은 run 명령의 `--only` 목록**에 넣으세요. 모델을 다운로드하거나 외부 서버를 시작하는 작업은 벤치마크에 포함하지 않습니다.

`configs/local.json`은 시작 후보 목록이며 최종 추천 순위가 아닙니다. 같은 모델의 목소리·steps·속도·추론 옵션도 각각의 provider로 만들어 비교할 수 있습니다.

## 결과 구조

```text
runs/<UTC 시각-ID>/
  manifest.json   # 실험 설정, 데이터/녹음 해시, 실행 코드 해시, 환경, 워커 패키지 버전
  samples.jsonl   # 각 요청의 입력·출력·원시 지표·오류
  warmups.jsonl   # 본 측정에서 제외한 워밍업 결과
  samples.csv     # 필터링·추가 분석용 평면 데이터
  summary.json   # 실험별 통계와 청취 평가 집계
  report.md      # 비교 결과와 해석 범위
  reviews.jsonl  # 평가자·샘플별 평점 변경 이력
  audio/         # 생성된 PCM WAV
  logs/          # 워커 진단 로그
```

실패한 요청은 지연 통계에서 제외하지만 전체 시도 수와 실패율에는 포함합니다. 실패가 있으면 CLI 종료 코드는 1입니다. 모델명을 숨긴 평가는 메모리에 유지한 무작위 샘플 ID를 사용하며, 모델명은 결과 탭에서 확인합니다.

## 평가 원칙

- `first_text_ms`와 `audio_file_ready_ms`를 구분합니다. HTTP 첫 바이트를 첫 음성으로 표기하지 않습니다.
- 현재 TTS 어댑터는 **전체 WAV 완성 시간**을 측정합니다. 모델 자체의 스트리밍 가능 여부와 별개입니다.
- 같은 TTS 문장으로 음성 엔진을 비교하고, 같은 사용자 질문으로 전체 조합을 비교합니다. 조합에서는 LLM의 답변 길이가 달라질 수 있습니다.
- 워커 로딩은 별도 기록하고, 워밍업 후 같은 프로세스에서 반복합니다. `warmups: 0`은 `unwarmed`이며 서버·OS 캐시까지 초기화한 콜드 테스트가 아닙니다.
- 순차 실행으로 측정 간 자원 경쟁을 줄입니다. 전체 조합의 구성요소는 함께 적재합니다. 샘플 순서는 seed로 섞고, 실험 순서는 설정 순서를 따릅니다.
- 첫 한국어 데이터는 출발점입니다. 운영 결정을 위해 실제 업무 표현과 여러 사람의 녹음을 추가해야 합니다.
- 모델 라이선스, 개발 지원 상태, 정확한 리비전도 선정 근거에 함께 기록합니다. 설정에 쓴 revision은 모델 파일을 자동 검증했다는 뜻이 아닙니다.

[평가 설계와 단계별 확장](docs/EVALUATION.md) · [새 엔진 연결 규약](docs/ADAPTERS.md)

## 현재 범위와 다음 단계

현재 버전은 **파일 기반 반복 실험과 사람의 평가를 위한 실행 가능한 기반**입니다. 마이크에서 시작하는 실시간 음성 대화, 오디오 청크 스트리밍, 음성 종료 감지, 끼어들기, 최대 GPU 메모리 샘플링, 여러 실행 간 통합 대시보드는 아직 구현하지 않았습니다. 이를 측정하지 않은 결과를 ‘실제 대화 지연’으로 표시하지 않습니다.
