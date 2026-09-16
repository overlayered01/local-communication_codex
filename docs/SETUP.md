# 엔진별 설치와 실행

Windows PowerShell, 저장소 루트 기준입니다. 평가 도구는 Python 3.11 이상으로 실행합니다. 모델 의존성은 별도 환경에 설치합니다. 아래 외부 엔진 설정은 공식 API에 맞춘 연결 예시이며, 이 저장소의 단위 테스트는 실제 모델을 다운로드하지 않습니다.

## Supertonic 3

공개 저장소는 보관 상태입니다. 기존 모델을 로컬에서 실행하되 아래 코드·가중치 스냅샷을 고정합니다. 예전 자동 다운로드 주소에 의존하지 않습니다.

```powershell
uv venv --python 3.12 .venv-supertonic
uv pip install --python .venv-supertonic/Scripts/python.exe "git+https://github.com/supertone-oss-archive/supertonic-py.git@df0f9686dac7fbbde391b759e2ee5286a3737622" huggingface_hub
.venv-supertonic/Scripts/hf.exe download supertone-oss-archive/supertonic-3 --revision aafc6e32416a594460b32413efc49d7fe4ce6d46 --local-dir models/supertonic-3
python -m conversation_lab run configs/local.json --only supertonic-5 supertonic-8 supertonic-12
```

기본 목소리는 F1, 속도는 1.0입니다. 목소리 비교는 provider를 복사해 `voice`를 변경하세요. 속도를 높인 결과는 말하기 자체도 빨라지므로 음성 길이와 RTF를 함께 확인합니다.

공식 근거: [보관본 실행 안내](https://github.com/supertone-oss-archive/supertonic#quick-start), [Python SDK](https://github.com/supertone-oss-archive/supertonic-py).

## Qwen3-TTS

```powershell
uv venv --python 3.12 .venv-qwen
uv pip install --python .venv-qwen/Scripts/python.exe qwen-tts soundfile huggingface_hub
# 해당 GPU를 지원하는 PyTorch CUDA 빌드를 설치했는지 먼저 확인합니다.
.venv-qwen/Scripts/python.exe -c "import torch; print(torch.__version__, torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CUDA unavailable')"
.venv-qwen/Scripts/hf.exe download Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice --local-dir models/Qwen3-TTS-12Hz-0.6B-CustomVoice
.venv-qwen/Scripts/hf.exe download Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice --local-dir models/Qwen3-TTS-12Hz-1.7B-CustomVoice
python -m conversation_lab run configs/local.json --only qwen-06 qwen-17
```

설치 환경에 맞는 CUDA 빌드 선택은 [PyTorch 설치 안내](https://pytorch.org/get-started/locally/)를 따릅니다. 일반 `pip install` 결과만으로 RTX 5080 지원을 보장하지 않습니다. 예시는 Windows에서 FlashAttention 빌드 의존성을 줄이기 위해 `sdpa`를 지정합니다. 이 설정의 속도는 논문에서 최적화한 스트리밍 엔진의 속도와 다릅니다.

품질 비교 시 1.7B의 `instruct`를 제거한 기준 실험과, 감정 지시를 추가한 실험을 나누세요. 서로 다른 제어 기능까지 포함하는 ‘실사용 설정 비교’와 모델 크기만 바꾸는 ‘통제 비교’를 구분합니다.

운영 선정을 위한 재실험에서는 다운로드에 `--revision <실제 커밋>`을 추가하고 설정에 해당 리비전을 남기세요. 현재 예시는 최신 모델 다운로드이며 고정 가중치 재현성을 보장하지 않습니다.

공식 근거: [Qwen CustomVoice API](https://github.com/QwenLM/Qwen3-TTS#custom-voice-generate).

## Ollama / 호환 LLM 서버

Ollama를 설치·실행한 뒤:

```powershell
ollama pull qwen3.5:4b
ollama pull qwen3.5:9b
python -m conversation_lab run configs/local.json --only qwen35-4b qwen35-9b
```

설정은 워밍업 동안 모델을 유지하고 실험 종료 시 해당 모델의 unload를 요청합니다(`keep_alive: -1`, `unload_after: true`). 같은 서버를 다른 프로그램이 사용하고 있으면 자원 경쟁이 생기므로 비교 환경을 정리하세요. 다른 엔진 서버의 모델 적재·해제는 해당 서버에서 관리합니다.

LM Studio 등 SSE 호환 서버는 `chat_sse` provider의 URL과 실제 모델 ID를 바꾼 뒤, 해당 provider를 사용하는 experiment를 추가합니다. 서버 버전, 모델 파일·양자화, 프롬프트 템플릿, 모델 digest는 운영 선정 기록에 따로 보관하세요. 현재 도구가 서버 내부 버전을 자동 판별하지는 않습니다.

공식 근거: [Ollama chat](https://docs.ollama.com/api/chat), [LM Studio 서버](https://lmstudio.ai/docs/developer/core/server).

## STT

```powershell
uv venv --python 3.12 .venv-whisper
uv pip install --python .venv-whisper/Scripts/python.exe faster-whisper huggingface_hub
.venv-whisper/Scripts/hf.exe download Systran/faster-whisper-small --local-dir models/faster-whisper-small
```

`datasets/recordings/recording-01.wav`에 실제 **PCM WAV**를 넣고 `datasets/ko-stt.example.jsonl`의 `reference`를 정확한 전사로 수정합니다. 예제 경로에 녹음이 없으면 검증이 실패하는 것이 정상입니다. 서로 다른 화자·소음·말하기 속도·실제 질문을 추가하세요.

```powershell
python -m conversation_lab validate configs/stt.example.json
python -m conversation_lab run configs/stt.example.json --only stt-small
```

전체 파이프라인은 STT가 인식한 텍스트를 LLM에 전달합니다. 이 경로는 완성된 녹음 파일에서 시작하며, 실제 사용자의 발화 종료 판단 시간은 포함하지 않습니다.

공식 근거: [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
