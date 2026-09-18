"""Allowlisted, engine-specific generation options shared by API and UI."""
import copy
import math

VOICE_STYLES = {
    '직접 지시만': '',
    '차분함': '차분하고 안정된 어조로, 서두르지 않고 여유 있게 말하세요.',
    '친근함': '가까운 사람과 이야기하듯 따뜻하고 친근한 어조로 말하세요.',
    '활기참': '밝고 활기찬 어조로, 경쾌한 리듬과 생동감 있는 억양으로 말하세요.',
    '공감': '상대의 마음을 헤아리듯 부드럽고 다정하게, 위로하는 어조로 말하세요.',
    '뉴스': '뉴스 진행자처럼 명료하고 단정하게, 중요한 정보를 또렷하게 전달하세요.',
    '내레이션': '이야기를 들려주는 내레이터처럼 자연스러운 흐름과 문장 사이 쉼을 살려 말하세요.',
}
VOICE_INTENSITIES = {
    1: '말투의 특징을 아주 은은하게 표현하고 감정 변화는 최소화하세요.',
    2: '말투의 특징과 감정을 절제해서 부드럽게 표현하세요.',
    3: '말투의 특징과 감정을 자연스럽고 적당한 강도로 표현하세요.',
    4: '말투의 특징과 감정이 분명히 느껴지도록 표현하세요.',
    5: '말투의 특징과 감정을 풍부하고 강하게 표현하되 발음의 명료함을 유지하세요.',
}


def field(key, label, default, path, *, low=None, high=None, choices=None, advanced=True):
    kind = 'select' if choices else 'boolean' if type(default) is bool else 'integer' if type(default) is int else 'number' if type(default) is float else 'text'
    return dict(key=key, label=label, default=default, path=path.split('.'), type=kind,
                min=low, max=high, choices=choices, advanced=advanced)


def schema(cfg):
    adapter, engine = cfg['adapter'], cfg.get('engine')
    fields = []
    if adapter in {'ollama', 'chat_sse'}:
        prefix = 'params.options.' if adapter == 'ollama' else 'params.'
        fields = [field('temperature', 'Temperature', 0.7, prefix+'temperature', low=0, high=2, advanced=False),
                  field('max_tokens', '답변 최대 토큰', 512, prefix+('num_predict' if adapter == 'ollama' else 'max_tokens'), low=16, high=4096, advanced=False),
                  field('top_p', 'Top-P', 0.9, prefix+'top_p', low=0.01, high=1)]
        if adapter == 'ollama':
            fields += [field('context', '컨텍스트 길이', 4096, prefix+'num_ctx', low=512, high=32768),
                       field('top_k', 'Top-K', 40, prefix+'top_k', low=1, high=200),
                       field('repeat_penalty', '반복 억제', 1.1, prefix+'repeat_penalty', low=0.5, high=2),
                       field('seed', 'Seed (-1: 무작위)', -1, prefix+'seed', low=-1, high=2147483647)]
            if cfg.get('model', '').startswith('qwen3'):
                fields += [field('think', '추론 모드', False, 'params.think')]
        else:
            fields += [field('frequency_penalty', '빈도 패널티', 0.0, 'params.frequency_penalty', low=-2, high=2),
                       field('presence_penalty', '존재 패널티', 0.0, 'params.presence_penalty', low=-2, high=2)]
    elif engine == 'supertonic':
        fields = [field('voice', '목소리', 'F1', 'voice', choices=[f'{g}{i}' for g in 'FM' for i in range(1,6)], advanced=False),
                  field('speed', '말하기 속도', 1.0, 'params.speed', low=0.7, high=2, advanced=False),
                  field('steps', '생성 Steps', 8, 'params.total_steps', low=1, high=50),
                  field('language', '언어', 'ko', 'params.lang', choices=['ko','en','ja','zh','es','fr','de']),
                  field('silence', '문장 사이 쉼 (초)', 0.3, 'params.silence_duration', low=0, high=2)]
    elif engine == 'qwen_tts':
        fields = [field('speaker', '목소리', 'Sohee', 'params.speaker', choices=['Sohee','Vivian','Serena','Uncle_Fu','Dylan','Eric','Ryan','Aiden','Ono_Anna'], advanced=False),
                  field('language', '언어', 'Korean', 'params.language', choices=['Auto','Korean','English','Chinese','Japanese','German','French','Russian','Portuguese','Spanish','Italian'], advanced=False),
                  field('temperature', 'Temperature', 0.9, 'params.temperature', low=0.1, high=2),
                  field('top_p', 'Top-P', 1.0, 'params.top_p', low=0.01, high=1),
                  field('top_k', 'Top-K', 50, 'params.top_k', low=1, high=200),
                  field('sample', '샘플링 사용', True, 'params.do_sample'),
                  field('repeat_penalty', '반복 억제', 1.05, 'params.repetition_penalty', low=0.5, high=2),
                  field('max_tokens', '최대 음성 토큰', 1024, 'params.max_new_tokens', low=64, high=4096)]
        if '1.7B' in cfg.get('model', ''):
            style = field('style', '말투 프리셋', '직접 지시만', 'voice_style.preset', choices=list(VOICE_STYLES), advanced=False)
            style['instructions'] = VOICE_STYLES
            intensity = field('intensity', '감정·표현 강도', 3, 'voice_style.intensity', low=1, high=5, advanced=False)
            intensity.update(widget='range', instructions=VOICE_INTENSITIES)
            fields += [style, intensity, field('instruct', '직접 감정·말투 지시', '', 'params.instruct', high=1000, advanced=False)]
    elif engine == 'faster_whisper':
        fields = [field('language', '인식 언어 (auto: 자동)', 'ko', 'params.language', choices=['auto','ko','en','ja','zh','de','fr','es'], advanced=False),
                  field('vad', '무음 구간 제외 (VAD)', True, 'params.vad_filter', advanced=False),
                  field('beam', 'Beam 크기', 5, 'params.beam_size', low=1, high=10),
                  field('temperature', 'Temperature', 0.0, 'params.temperature', low=0, high=1),
                  field('previous', '이전 구간 문맥 사용', True, 'params.condition_on_previous_text'),
                  field('hint', '고유명사·인식 힌트', '', 'params.initial_prompt', high=1000, advanced=False),
                  field('silence_ms', '무음 구간 기준 (ms)', 500, 'params.vad_parameters.min_silence_duration_ms', low=100, high=3000)]
    for f in fields:
        value = cfg
        for part in f['path']:
            value = value.get(part, {}) if isinstance(value, dict) else {}
        if value != {} and value is not None:
            f['default'] = value
    return fields


def apply_options(cfg, supplied):
    if not isinstance(supplied, dict):
        raise ValueError('모델 옵션은 객체여야 합니다')
    fields = {f['key']: f for f in schema(cfg)}
    if supplied.keys() - fields.keys():
        raise ValueError('선택한 모델이 지원하지 않는 옵션: '+', '.join(supplied.keys()-fields.keys()))
    result = copy.deepcopy(cfg)
    for key, value in supplied.items():
        f = fields[key]
        kind = f['type']
        if kind in {'integer','number'}:
            if type(value) not in ((int,) if kind == 'integer' else (int,float)) or not math.isfinite(value) or not f['min'] <= value <= f['max']:
                raise ValueError(f"{f['label']}: {f['min']}~{f['max']} 범위를 확인하세요")
        elif kind == 'boolean':
            if type(value) is not bool:
                raise ValueError(f"{f['label']}: 켜짐/꺼짐 값이 필요합니다")
        elif kind == 'select':
            if value not in f['choices']:
                raise ValueError(f"{f['label']}: 지원하지 않는 값입니다")
        elif not isinstance(value, str) or len(value) > (f['max'] or 4000):
            raise ValueError(f"{f['label']}: 입력 길이를 확인하세요")
        if cfg.get('engine') == 'faster_whisper' and key == 'language' and value == 'auto':
            value = None
        target = result
        for part in f['path'][:-1]:
            target = target.setdefault(part, {})
        target[f['path'][-1]] = value
    style = result.get('voice_style', {})
    if style.get('preset') in VOICE_STYLES and style['preset'] != '직접 지시만':
        direct = result.setdefault('params', {}).get('instruct', '').strip()
        instruction = VOICE_STYLES[style['preset']] + ' ' + VOICE_INTENSITIES[style.get('intensity', 3)]
        if direct:
            instruction += '\n추가 지시 (충돌하는 내용은 이 지시를 우선하세요): ' + direct
        result['params']['instruct'] = instruction
    return result


def public_settings(configs):
    return {stage: {key: copy.deepcopy(cfg[key]) for key in ('model','engine','adapter','system','params','voice','voice_style') if key in cfg}
            for stage, cfg in configs.items()}
