# 새 모델과 실행 도구 연결

## 설정으로 추가

`providers`는 모델과 추론 설정을 정의하고 `experiments`는 데이터셋과 조합을 선택합니다. 같은 엔진의 서로 다른 설정도 별도 provider입니다.

```json
{
  "id": "compare-new-tts",
  "mode": "tts",
  "tts": "my-tts",
  "dataset": "datasets/ko-tts.jsonl",
  "warmups": 1,
  "repetitions": 3
}
```

`root`는 설정 파일 디렉터리 기준입니다. 데이터셋은 root 기준이고, 녹음 경로는 JSONL 파일의 디렉터리 기준입니다. 워커 설정과 command 안의 `{root}`는 절대 경로로 치환됩니다. command의 `{python}`은 평가 도구를 실행한 Python입니다.

## 어댑터 선택

- `ollama`: 전체 `/api/chat` URL. `params.options` 등은 서버에 전달합니다.
- `chat_sse`: `/v1/chat/completions` 호환 SSE. 모델 ID와 옵션은 해당 서버 기준입니다. 모델 내부 추론 토큰은 표시용 텍스트와 구분합니다.
- `tts_http`: `/v1/audio/speech` 호환 서버. input, model, response_format=wav 및 params를 전달합니다. 반환은 비어 있지 않은 **PCM WAV**여야 합니다. 청크별 재생 타이밍은 미측정입니다.
- `worker`: JSONL 표준 입출력을 쓰는 독립 프로세스. 언어·프레임워크 제한 없이 연결할 수 있습니다.
- `mock`: 평가 흐름 전용 가짜 텍스트/톤. 모델 성능 평가에는 사용할 수 없습니다.

HTTP 인증이 필요하면 `api_key_env`에 환경변수 이름을 넣습니다. 비밀 값을 설정 파일에 직접 쓰지 않습니다. 기본은 localhost 엔드포인트만 허용하며 원격 서버 비교는 설정에서 `allow_remote: true`를 명시합니다. 코드가 자동으로 외부 API를 선택하지 않습니다.

## 워커 규약

프로세스 하나가 여러 요청을 처리합니다. stdin 한 줄당 stdout JSON 한 줄로 응답하며, 진단 로그는 stderr에 씁니다. Python print 로그가 섞이면 프로토콜 오류로 처리됩니다. 셸 문자열 대신 command 인수 배열을 사용합니다.

최초 요청:

```json
{"op":"load","config":{"engine":"my-engine","model":"..."}}
```

응답:

```json
{"metadata":{"engine":"my-engine","version":"1.0","model_revision":"exact-revision","device":"cpu"}}
```

추론 요청:

```json
{"op":"run","case":{"id":"greeting","text":"안녕하세요."},"output":"D:/.../audio/uuid.wav"}
```

TTS 응답:

```json
{"audio_path":"D:/.../audio/uuid.wav","metrics":{}}
```

LLM/STT 응답:

```json
{"text":"인식 또는 생성된 텍스트","metrics":{}}
```

오류는 `{"error":"설명"}`으로 응답합니다. 워커 제한 시간을 넘기면 해당 프로세스를 종료합니다. 실패 요청을 자동 재시도하거나 결과에서 삭제하지 않습니다. 이후 요청도 실패로 기록될 수 있으므로 복구 전략은 별도 실험으로 비교하세요.

`request_ms`, WAV 길이, RTF, CER/WER는 평가 도구가 계산합니다. 추가 지표는 정의와 단위를 문서화한 뒤 반환하세요. 모의 엔진이라면 metadata 또는 추론 응답에 `synthetic: true`를 반드시 선언합니다.

현재 워커 규약은 완성 응답 방식입니다. 스트리밍 엔진의 첫 오디오 청크를 측정하려면 향후 청크 이벤트·오디오 포맷·타임스탬프 규약을 추가해야 합니다. 완성 파일 시간을 첫 청크 시간으로 이름만 바꾸지 않습니다.
