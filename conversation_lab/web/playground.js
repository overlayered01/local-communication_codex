const $ = id => document.getElementById(id);
let mode = 'voice', models = [], history = [], busy = false, lastModel = '';
let recording = null, requestingMic = false;
const engineName = engine => ({ollama:'Ollama', chat_sse:'LM Studio / 호환 서버', supertonic:'Supertonic', qwen_tts:'Qwen TTS', tts_http:'음성 서버', faster_whisper:'Whisper'}[engine] || engine);

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || '요청을 처리하지 못했습니다.');
  return data;
}
function status(text) { $('status').textContent = text; $('voiceStatus').textContent = text; }
function flowStages() { return Object.fromEntries(['stt','llm','tts'].map(k=>[k,$('stage-'+k).checked])); }
function setFlow(stages) {
  for(const k of ['stt','llm','tts'])$('stage-'+k).checked=stages[k]===true;
  syncFlow();
}
function syncFlow() {
  const stages=flowStages();
  for(const k of ['stt','llm','tts']) {
    $(k+'Settings').hidden=!stages[k];
    $('flow-'+k).classList.toggle('stage-off',!stages[k]);
  }
  $('form').hidden=stages.stt;$('recorder').hidden=!stages.stt;
  $('conversationCommon').hidden=!stages.llm;$('autoplaySetting').hidden=!stages.tts;
  $('flowSummary').textContent=[stages.stt?'마이크 → 음성 인식':'텍스트 입력',stages.llm?'LLM 답변':'입력 텍스트 그대로',stages.tts?'음성 생성 → 재생':'텍스트 결과'].join(' → ');
  $('send').textContent='선택한 단계 실행 ↗';
  $('promptLabel').textContent=stages.llm?'질문 / 메시지':'음성으로 만들 텍스트';
  $('emptyTitle').textContent='음성 대화의 각 단계를 테스트하세요';
  $('emptyDescription').textContent=stages.stt?'녹음을 끝내면 켜진 단계만 순서대로 실행합니다.':'텍스트를 입력하면 켜진 단계만 순서대로 실행합니다.';
  document.querySelector('.chips').hidden=stages.stt;
  try {localStorage.setItem('local-studio-flow-v1',JSON.stringify(stages));}catch(_){}
  clear();ttsInfo();availability();
}
function availability() {
  const stages=flowStages();
  const ready=Object.values(stages).some(Boolean)&&Object.entries(stages).every(([k,on])=>!on||!!$(k).value);
  $('send').disabled=busy||!ready;$('record').disabled=busy||requestingMic||!ready;
  if(!busy&&!recording&&!requestingMic)status(ready?'준비됐습니다.':Object.values(stages).some(Boolean)?'켜진 단계의 모델 연결을 확인하세요.':'실행할 단계를 하나 이상 켜세요.');
}
function ttsInfo() {
  const m = models.find(m => m.id === $('tts').value);
  $('voiceControls').hidden = true;
  $('qwenControls').hidden = true;
  $('ttsInfo').textContent = m?.engine === 'supertonic' ? '5·8·12는 생성 steps 설정입니다. 목소리와 속도를 바꿔 직접 들어보세요.' : m?.reason || '사용 가능한 음성 모델을 준비해 주세요.';
}
async function refresh() {
  $('refresh').disabled = true;
  try {
    const data = await api('/api/models'); models = data.models;
    for (const kind of ['llm', 'tts', 'stt']) {
      const previous = $(kind).value; $(kind).replaceChildren();
      for (const m of models.filter(m => m.kind === kind)) {
        const option = new Option(`${m.label} · ${m.ready ? engineName(m.engine) : m.reason}`, m.id);
        option.disabled = !m.ready; $(kind).add(option);
      }
      const valid = models.filter(m => m.kind === kind && m.ready);
      if (!valid.length) $(kind).add(new Option('사용 가능한 모델 없음', ''));
      $(kind).value = valid.some(m => m.id === previous) ? previous : valid[0]?.id || '';
    }
    if (history.length && lastModel !== $('llm').value) { history = []; addMessage('안내', '모델 연결이 바뀌어 대화 맥락을 초기화했습니다.'); }
    lastModel = $('llm').value;
    $('connections').textContent = data.connections.map(c => `${engineName(c.engine)}: ${c.connected ? c.count + '개 모델' : c.message}`).join(' · ');
    ttsInfo(); availability();
    if (window.studioRefresh) await window.studioRefresh();
  } catch (e) { status(e.message); }
  finally { $('refresh').disabled = false; }
}
function addMessage(label, text, kind = '') {
  $('empty').hidden = true;
  const box = document.createElement('div'); box.className = 'message ' + kind;
  const by = document.createElement('div'); by.className = 'by'; by.textContent = label;
  const content = document.createElement('div'); content.className = 'body'; content.textContent = text;
  box.append(by, content); $('results').append(box); $('results').scrollTop = $('results').scrollHeight;
  return box;
}
function stopPlayback() { document.querySelectorAll('audio').forEach(a => a.pause()); }
function clear() {
  stopPlayback(); history = [];
  $('results').querySelectorAll('.message').forEach(x => x.remove()); $('empty').hidden = false;
  $('panelTitle').textContent = '음성 대화 · 실행 결과';
}
const lockedControls = '.tab,#refresh,#clear,#llm,#tts,#stt,#voice,#speed,#speaker,#temperature,#system,#prompt,.chip';
function lockControls(value) { document.querySelectorAll(lockedControls+',.feature-toggle,.model-option,.reset-options,#openRag,#openSettings,#applyPreset,#presetSelect,#ragEnabled,#ragCollection,#ragTopK,#previewSearch').forEach(el => el.disabled = value); }
function setBusy(value) { busy = value; lockControls(value); availability(); }
$('llm').onchange = () => { lastModel = $('llm').value; clear(); availability(); };
$('tts').onchange = () => { ttsInfo(); availability(); };
$('stt').onchange = availability;
$('system').onchange = () => { clear(); availability(); };
$('refresh').onclick = refresh; $('clear').onclick = clear;
for(const k of ['stt','llm','tts'])$('stage-'+k).onchange=syncFlow;
try {const stored=JSON.parse(localStorage.getItem('local-studio-flow-v1'));if(stored&&['stt','llm','tts'].every(k=>typeof stored[k]==='boolean'))setFlow(stored);else syncFlow();}catch(_){syncFlow();}

document.querySelectorAll('[data-prompt]').forEach(b => b.onclick = () => { $('prompt').value = b.dataset.prompt; $('prompt').focus(); });

function renderTrace(box,job) {
  const body=box.querySelector('.body');
  for(const k of ['stt','llm','tts']) {
    let details=body.querySelector(`[data-stage="${k}"]`);
    if(!details){details=document.createElement('details');details.dataset.stage=k;details.open=true;details.append(document.createElement('summary'),document.createElement('pre'));body.append(details);}
    const t=job.stages?.[k]||{status:flowStages()[k]?'queued':'skipped'};
    details.querySelector('summary').textContent=`${{stt:'음성 인식',llm:'LLM 답변',tts:'음성 생성'}[k]} · ${{queued:'대기',running:'실행 중',done:'완료',skipped:'꺼짐 · 건너뜀',error:'실패',blocked:'앞 단계 실패로 중단'}[t.status]}${t.elapsed_ms!==undefined?' · '+(t.elapsed_ms/1000).toFixed(2)+'초':''}`;
    details.querySelector('pre').textContent=[t.input!==undefined?'입력: '+t.input:'',t.output!==undefined?'출력: '+t.output:'',t.error||''].filter(Boolean).join('\n');
  }
}
async function generate(text, audioBase64 = null) {
  if (busy) return;
  const stages=flowStages();
  stopPlayback(); setBusy(true); status('요청을 보내고 있습니다…');
  const userBox = addMessage('나', stages.stt ? '녹음한 내용을 인식하고 있습니다…' : text, 'user');
  const model = models.find(m => m.id === $(stages.llm ? 'llm' : stages.tts ? 'tts' : 'stt').value);
  let replyBox = null, transcript = text;
  try {
    const request = {mode, stages, text, llm:$('llm').value, tts:$('tts').value, stt:$('stt').value,
      messages:!stages.llm || window.studioHistory?.() === false ? [] : history.slice(-40), system:$('system').value,
      temperature:Number($('temperature').value), voice:$('voice').value, speed:Number($('speed').value), speaker:$('speaker').value};
    if (window.studioRequest) Object.assign(request, window.studioRequest());
    if (audioBase64) request.audio_base64 = audioBase64;
    const {id} = await api('/api/generate', request);
    const traceBox=addMessage('단계별 실행 상태','');
    let job;
    do {
      await new Promise(resolve => setTimeout(resolve, 350));
      job = await api('/api/jobs/' + id); status(job.phase || '실행 중…');
      if (job.transcript) { transcript = job.transcript; userBox.querySelector('.body').textContent = transcript; }
      renderTrace(traceBox,job);
    } while (job.status === 'running');
    if (job.status === 'error') throw Error(job.error);
    replyBox ||= addMessage(model.label, job.text || text);
    if (window.studioResult) window.studioResult(replyBox, job);
    if (job.audio) {
      const autoplay = window.studioAutoplay?.() !== false;
      const audio = document.createElement('audio'); audio.controls = true; audio.autoplay = autoplay; audio.src = job.audio;
      const link = document.createElement('a'); link.href = job.audio; link.download = 'speech-' + id + '.wav'; link.className = 'download'; link.textContent = 'WAV 다운로드';
      replyBox.append(audio, link);
      try { if(autoplay) await audio.play(); }
      catch (_) { const note = document.createElement('p'); note.className = 'small'; note.textContent = '브라우저가 자동 재생을 차단했습니다. 위 재생 버튼을 눌러주세요.'; replyBox.append(note); }
    }
    const timing = document.createElement('div'); timing.className = 'small'; timing.textContent = `완료까지 ${(job.elapsed_ms / 1000).toFixed(2)}초 (로딩 포함)`; replyBox.append(timing);
    if (stages.llm) history.push({role:'user', content:transcript}, {role:'assistant', content:job.text});
    $('prompt').value = ''; $('panelTitle').textContent = model.label;
    $('results').scrollTop = $('results').scrollHeight;
  } catch (e) { addMessage('실행 오류', e.message, 'error'); }
  finally { setBusy(false); if (!stages.stt) $('prompt').focus(); }
}
$('form').onsubmit = e => { e.preventDefault(); const text = $('prompt').value.trim(); if (text && !busy) generate(text); };
$('prompt').placeholder = 'Enter로 전송 · Shift+Enter 또는 Ctrl+Enter로 줄바꿈';
$('prompt').onkeydown = e => {
  if (e.key !== 'Enter' || e.isComposing || e.keyCode === 229) return;
  if (e.shiftKey) return;
  if (e.ctrlKey || e.metaKey) {
    e.preventDefault();
    const input = e.currentTarget;
    if (input.value.length - (input.selectionEnd - input.selectionStart) < input.maxLength) {
      input.setRangeText('\n', input.selectionStart, input.selectionEnd, 'end');
      input.dispatchEvent(new Event('input', {bubbles:true}));
    }
    return;
  }
  e.preventDefault();
  if (!$('send').disabled) $('form').requestSubmit();
};

function encodeWav(chunks, sampleRate) {
  const length = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2), view = new DataView(buffer);
  const write = (offset, text) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + length * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const sample of chunk) { const s = Math.max(-1, Math.min(1, sample)); view.setInt16(offset, s < 0 ? s * 32768 : s * 32767, true); offset += 2; }
  let binary = ''; const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
  return btoa(binary);
}
async function startRecording() {
  if (busy || requestingMic) return;
  stopPlayback(); requestingMic = true; lockControls(true); availability(); status('마이크 권한을 확인하고 있습니다…');
  let stream, context;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw Error('이 브라우저에서 마이크를 사용할 수 없습니다. localhost에서 Chrome 또는 Edge로 열어주세요.');
    stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1, echoCancellation:true, noiseSuppression:true}, video:false});
    context = new AudioContext({sampleRate:48000}); await context.resume();
    const source = context.createMediaStreamSource(stream), node = context.createScriptProcessor(4096, 1, 1), mute = context.createGain();
    mute.gain.value = 0;
    const state = {stream, context, source, node, mute, chunks:[], frames:0, started:Date.now()};
    recording = state;
    node.onaudioprocess = e => {
      if (recording !== state) return;
      const input = e.inputBuffer.getChannelData(0), count = Math.min(input.length, 60 * context.sampleRate - state.frames);
      if (count > 0) { state.chunks.push(new Float32Array(input.subarray(0, count))); state.frames += count; }
      if (state.frames >= 60 * context.sampleRate) finishRecording(true);
    };
    source.connect(node); node.connect(mute); mute.connect(context.destination);
    state.timer = setInterval(() => { $('recordTime').textContent = Math.min(60, Math.floor((Date.now() - state.started) / 1000)) + '초 / 60초'; }, 250);
    state.limit = setTimeout(() => finishRecording(true), 60000);
    $('record').textContent = '■ 녹음 끝내고 보내기'; $('cancelRecording').hidden = false;
    status('듣고 있습니다. 말씀을 마치면 녹음 끝내고 보내기를 누르세요.');
  } catch (e) {
    stream?.getTracks().forEach(t => t.stop()); if (context) await context.close();
    lockControls(false); addMessage('마이크 안내', e.name === 'NotAllowedError' ? '마이크 권한이 거부됐습니다. 브라우저의 사이트 설정에서 마이크를 허용한 뒤 다시 시도하세요.' : e.message, 'error');
  } finally { requestingMic = false; availability(); }
}
async function finishRecording(send) {
  const state = recording; if (!state) return;
  recording = null; requestingMic = true; availability(); clearInterval(state.timer); clearTimeout(state.limit);
  state.node.onaudioprocess = null; state.source.disconnect(); state.node.disconnect(); state.mute.disconnect();
  state.stream.getTracks().forEach(t => t.stop());
  const audio = send && state.frames >= state.context.sampleRate / 10 ? encodeWav(state.chunks, state.context.sampleRate) : null;
  await state.context.close();
  requestingMic = false;
  $('record').textContent = '● 녹음 시작'; $('cancelRecording').hidden = true; $('recordTime').textContent = '최대 60초';
  lockControls(false); availability();
  if (audio) await generate('', audio);
  else if (send) status('녹음이 너무 짧습니다. 다시 말씀해 주세요.');
}
$('record').onclick = () => recording ? finishRecording(true) : startRecording();
$('cancelRecording').onclick = () => finishRecording(false);
window.addEventListener('pagehide', () => { recording?.stream.getTracks().forEach(t => t.stop()); stopPlayback(); });
refresh();
