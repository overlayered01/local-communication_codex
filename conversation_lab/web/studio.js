(() => {
  const storageKey = 'local-studio-options-v1';
  let saved = {}, library = {presets:[],collections:[],embeddings:[]}, active = {}, initialized = false;
  try { saved = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch (_) {}
  if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = {};
  function persist() { try { localStorage.setItem(storageKey,JSON.stringify(saved)); } catch (_) {} }
  function model(kind) { return models.find(m => m.id === $(kind).value); }
  function readOptions(kind) {
    const value = {};
    document.querySelectorAll(`[data-kind="${kind}"].model-option`).forEach(input => {
      value[input.dataset.key] = input.type === 'checkbox' ? input.checked : input.dataset.numeric ? Number(input.value) : input.value;
    });
    return value;
  }
  function remember(kind) {
    const key = active[kind]; if (!key) return;
    saved[key] = {options:readOptions(kind), ...(kind === 'llm' ? {system:$('system').value} : {})};
    persist();
  }
  function render(kind) {
    let holder = $(kind+'Options');
    if (!holder) { holder = document.createElement('section'); holder.id = kind+'Options'; $(kind+'Settings').append(holder); }
    holder.replaceChildren();
    const m = model(kind); active[kind] = m?.id || ''; if (!m) return;
    if (kind === 'llm') $('system').value = saved[m.id]?.system ?? '한국어로 자연스럽고 간결하게 답하세요.';
    const normal = document.createElement('div'); normal.className = 'option-grid';
    const advanced = document.createElement('details'); const summary = document.createElement('summary'); summary.textContent = '고급 옵션'; advanced.append(summary);
    const extra = document.createElement('div'); extra.className = 'option-grid'; advanced.append(extra);
    for (const f of m.options || []) {
      const label = document.createElement('label'); label.className = 'label'; label.textContent = f.label;
      const input = document.createElement(f.type === 'select' ? 'select' : f.type === 'text' ? 'textarea' : 'input');
      if (f.type === 'select') f.choices.forEach(c => input.add(new Option(c,c)));
      else if (f.type === 'boolean') input.type = 'checkbox';
      else if (f.type === 'text') { input.rows = 3; input.maxLength = f.max || 4000; label.classList.add('option-wide'); }
      else { input.type = 'number'; input.min = f.min; input.max = f.max; input.step = f.type === 'integer' ? '1' : '0.01'; input.dataset.numeric = 'true'; }
      if (f.widget === 'range') { input.type = 'range'; label.classList.add('option-wide'); }
      const value = saved[m.id]?.options?.[f.key] ?? f.default;
      if (f.type === 'boolean') input.checked = value; else input.value = value;
      input.className = 'model-option'; input.dataset.kind = kind; input.dataset.key = f.key;
      input.id = `option-${kind}-${f.key}`; label.htmlFor = input.id;
      input.onchange = () => remember(kind);
      label.append(input); (f.advanced ? extra : normal).append(label);
      if (f.widget === 'range') {
        const output = document.createElement('output'); output.className = 'small'; output.id = input.id+'-value'; output.htmlFor = input.id;
        const update = () => { output.textContent = `${input.value} / 5 · ${['아주 은은하게','절제해서','자연스럽게','분명하게','풍부하게'][Number(input.value)-1]}`; };
        input.addEventListener('input',update); update(); label.append(output);
      }
    }
    holder.append(normal); if (extra.children.length) holder.append(advanced);
    const styleField = m.options?.find(f => f.key === 'style');
    if (styleField) {
      const note = document.createElement('p'); note.className = 'small';
      note.textContent = '말투와 강도를 자연어 지시로 변환합니다. 정확한 음향 수치는 아니며, 충돌하면 직접 지시를 우선합니다. ‘직접 지시만’에서는 강도를 적용하지 않습니다.';
      const preview = document.createElement('details'), heading = document.createElement('summary'), content = document.createElement('p');
      heading.textContent = '적용할 말투 지시 미리보기'; content.className = 'small'; content.style.whiteSpace = 'pre-wrap'; preview.append(heading,content);
      const update = () => {
        const values = readOptions(kind), intensity = m.options.find(f=>f.key==='intensity');
        const preset = styleField.instructions[values.style];
        content.textContent = preset ? preset+' '+intensity.instructions[values.intensity]+(values.instruct.trim()?'\n추가 지시 (충돌하는 내용은 이 지시를 우선하세요): '+values.instruct.trim():'') : values.instruct || '추가 말투 지시 없음';
      };
      normal.addEventListener('input',update); normal.addEventListener('change',update);
      holder.append(note,preview); update();
    }
    const reset = document.createElement('button'); reset.className = 'secondary reset-options'; reset.textContent = '모델 옵션 기본값 복원'; reset.style.marginTop = '12px';
    reset.onclick = () => { saved[m.id] = {...saved[m.id],options:{}}; render(kind); remember(kind); };
    if (m.options?.length) holder.append(reset);
  }
  function sourcesView(container,sources) {
    container.replaceChildren();
    if (!sources.length) { const text = document.createElement('p'); text.textContent = '관련 근거를 찾지 못했습니다.'; text.className = 'small'; container.append(text); }
    sources.forEach((s,i) => { const box = document.createElement('div'); box.className = 'source'; box.textContent = `[${i+1}] ${s.name} · ${s.page}페이지 · 조각 ${s.part}\n${s.text}`; container.append(box); });
  }
  function ragSettings() { return {enabled:$('ragEnabled').checked,collection:$('ragCollection').value,top_k:Number($('ragTopK').value)}; }
  function capture() {
    for (const kind of ['llm','tts','stt']) remember(kind);
    return {version:1,mode,models:Object.fromEntries(['llm','tts','stt'].map(k=>[k,$(k).value])),modelSettings:structuredClone(saved),system:$('system').value,rag:ragSettings()};
  }
  function populate(select,rows,placeholder) {
    const old = select.value; select.replaceChildren(new Option(placeholder,''));
    rows.forEach(r => select.add(new Option(r.name,r.id))); select.value = rows.some(r=>r.id===old) ? old : '';
  }
  function renderCollection() {
    const c = library.collections.find(c=>c.id===$('manageCollection').value);
    $('documents').replaceChildren();
    if (!c) return;
    $('embedding').value = c.embedding; $('chunkSize').value = c.size; $('chunkOverlap').value = c.overlap;
    for (const d of c.documents) {
      const row = document.createElement('div'); row.className='document-row';
      const label = document.createElement('span'); label.className='small'; label.textContent=`${d.name} · 색인 완료 · ${d.chunks}조각`;
      const remove = document.createElement('button'); remove.className='secondary'; remove.textContent='삭제';
      remove.onclick=()=>perform('문서 삭제 중…',async()=>update(await api('/api/library/document-delete',{id:d.id})));
      row.append(label,remove); $('documents').append(row);
    }
  }
  function update(data) {
    library=data;
    populate($('presetSelect'),library.presets,'프리셋 선택');
    populate($('ragCollection'),library.collections,'문서 모음 선택');
    populate($('manageCollection'),library.collections,'문서 모음 선택');
    const embedding=$('embedding').value;
    $('embedding').replaceChildren(...library.embeddings.map(e=>new Option(e.label,e.id)));
    $('embedding').value=library.embeddings.some(e=>e.id===embedding)?embedding:'lexical';
    renderCollection();
  }
  async function perform(message,fn) {
    const controls=[...$('studioSettings').querySelectorAll('button,input,select,textarea')].filter(e=>e.id!=='closeSettings');
    controls.forEach(e=>e.disabled=true); $('libraryStatus').textContent=message;
    try { await fn(); $('libraryStatus').textContent='완료했습니다.'; }
    catch(e) { $('libraryStatus').textContent=e.message; }
    finally { controls.forEach(e=>e.disabled=false); }
  }
  window.studioRefresh=async()=>{
    for(const kind of ['llm','tts','stt'])render(kind);
    update(await api('/api/library')); initialized=true;
  };
  window.studioRequest=()=>{
    const options={};
    for(const kind of ['llm','tts','stt']){remember(kind);options[kind]=readOptions(kind);}
    return {options,rag:ragSettings()};
  };
  window.studioResult=(box,job)=>{
    if(job.rag?.enabled){const details=document.createElement('details');const title=document.createElement('summary');title.textContent=`검색된 근거 (${job.sources.length})`;const content=document.createElement('div');sourcesView(content,job.sources);details.append(title,content);box.append(details);}
    const details=document.createElement('details'),title=document.createElement('summary'),content=document.createElement('pre');
    title.textContent='실제 실행 설정';content.className='settings-view';content.textContent=JSON.stringify({models:job.settings,rag:job.rag},null,2);details.append(title,content);box.append(details);
  };
  for(const kind of ['llm','tts','stt'])$(kind).addEventListener('change',()=>render(kind));
  $('system').addEventListener('change',()=>remember('llm'));
  document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>{$('ragSettings').hidden=mode==='tts';}));
  for(const id of ['ragEnabled','ragCollection','ragTopK'])$(id).addEventListener('change',()=>clear());
  $('openSettings').onclick=()=>{capture();$('systemEditor').value=$('system').value;$('studioSettings').showModal();};
  $('closeSettings').onclick=()=>$('studioSettings').close();
  $('applySystem').onclick=()=>{$('system').value=$('systemEditor').value;remember('llm');clear();$('libraryStatus').textContent='프롬프트를 적용하고 새 대화를 시작했습니다.';};
  $('presetSelect').onchange=()=>{$('presetName').value=library.presets.find(p=>p.id===$('presetSelect').value)?.name||'';};
  $('applyPreset').onclick=()=>{
    const preset=library.presets.find(p=>p.id===$('presetSelect').value);if(!preset){status('적용할 프리셋을 선택하세요.');return;}
    const value=preset.settings;
    document.querySelector(`[data-mode="${['chat','tts','pipeline','voice'].includes(value.mode)?value.mode:'chat'}"]`).click();
    saved={...saved,...value.modelSettings};persist();const missing=[];
    for(const kind of ['llm','tts','stt']){const id=value.models?.[kind]||'';$(kind).value=id;if(id&&!$(kind).value)missing.push(kind);render(kind);}
    $('system').value=value.system||'';remember('llm');$('ragEnabled').checked=!!value.rag?.enabled;$('ragCollection').value=value.rag?.collection||'';$('ragTopK').value=value.rag?.top_k||3;
    lastModel=$('llm').value;clear();ttsInfo();availability();
    if(missing.length)status('프리셋 모델이 현재 연결되지 않았습니다: '+missing.join(', '));
    if($('ragEnabled').checked&&!$('ragCollection').value)status('프리셋의 문서 모음이 없습니다. 다시 선택해 주세요.');
  };
  async function savePreset(copy) {
    await perform('프리셋 저장 중…',async()=>{
      $('system').value=$('systemEditor').value;remember('llm');clear();
      const name=$('presetName').value;
      const state=await api('/api/library/preset-save',{id:copy?'':$('presetSelect').value,name,settings:capture()});
      update(state);const chosen=[...library.presets].reverse().find(p=>p.name===name.trim());if(chosen)$('presetSelect').value=chosen.id;
    });
  }
  $('savePreset').onclick=()=>savePreset(false);$('copyPreset').onclick=()=>savePreset(true);
  $('deletePreset').onclick=()=>perform('프리셋 삭제 중…',async()=>update(await api('/api/library/preset-delete',{id:$('presetSelect').value})));
  $('manageCollection').onchange=renderCollection;
  const indexSettings=()=>({embedding:$('embedding').value,size:Number($('chunkSize').value),overlap:Number($('chunkOverlap').value)});
  $('createCollection').onclick=()=>perform('문서 모음 생성 중…',async()=>{
    const name=$('collectionName').value;update(await api('/api/library/collection-create',{name,...indexSettings()}));
    const c=[...library.collections].reverse().find(c=>c.name===name.trim());if(c){$('manageCollection').value=c.id;$('ragCollection').value=c.id;renderCollection();}
  });
  $('reindexCollection').onclick=()=>perform('문서 전체를 재색인하고 있습니다…',async()=>{update(await api('/api/library/reindex',{id:$('manageCollection').value,...indexSettings()}));clear();});
  $('deleteCollection').onclick=()=>perform('문서 모음 삭제 중…',async()=>{update(await api('/api/library/collection-delete',{id:$('manageCollection').value}));clear();});
  $('uploadFiles').onchange=()=>perform('문서 텍스트 추출·색인 중…',async()=>{
    for(const file of $('uploadFiles').files){
      if(file.size>5_000_000)throw Error('파일당 최대 5MB입니다.');
      const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('파일을 읽지 못했습니다'));reader.readAsDataURL(file);});
      update(await api('/api/library/upload',{collection:$('manageCollection').value,name:file.name,content}));
    }
    $('uploadFiles').value='';clear();
  });
  $('previewSearch').onclick=async()=>{try{$('previewSearch').disabled=true;const result=await api('/api/library/search',{query:$('prompt').value,rag:{...ragSettings(),enabled:true}});sourcesView($('searchPreview'),result.sources);}catch(e){$('searchPreview').textContent=e.message;}finally{$('previewSearch').disabled=false;}};
  // The model fetch can finish before this script has loaded on a very fast local server.
  if(models.length&&!initialized)window.studioRefresh().catch(e=>status(e.message));
})();
