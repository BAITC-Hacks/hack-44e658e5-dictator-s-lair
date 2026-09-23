const $=id=>document.getElementById(id);
let state={data:null,audioUrl:null,audioFile:null,fileName:'',processing:false,restoring:false,historyId:null,historyRecord:null,historySaved:false,historyUnsaved:false};
let historyWrites=Promise.resolve();
let pendingHistoryWrites=0;
const fmtTime=s=>{s=Math.max(0,Number(s)||0);return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(Math.floor(s%60)).padStart(2,'0')}`};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const API_BASE='http://127.0.0.1:8000';
function normalize(raw){
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const invalid=field=>{throw new Error(`Backend returned malformed meeting JSON (${field}). Повторите обработку записи.`)};
  const timestamp=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
  if(!object(raw))invalid('response');
  if(!object(raw.meeting))invalid('meeting');
  if(!object(raw.summary))invalid('summary');
  if(!Array.isArray(raw.summary.key_topics)||!raw.summary.key_topics.every(x=>typeof x==='string'))invalid('summary.key_topics');
  if(!Array.isArray(raw.summary.decisions)||!raw.summary.decisions.every(x=>typeof x==='string'||(object(x)&&typeof(x.decision??x.text)==='string')))invalid('summary.decisions');
  if(!Array.isArray(raw.action_items))invalid('action_items');
  if(!Array.isArray(raw.transcript))invalid('transcript');
  raw.action_items.forEach((x,i)=>{
    if(!object(x)||typeof x.task!=='string'||typeof x.source_quote!=='string')invalid(`action_items[${i}]`);
    if(!timestamp(x.timestamp_start)||!timestamp(x.timestamp_end)||x.timestamp_end<x.timestamp_start)invalid(`action_items[${i}].timestamps`);
  });
  raw.transcript.forEach((x,i)=>{
    if(!object(x)||typeof x.text!=='string')invalid(`transcript[${i}]`);
    if(!timestamp(x.start)||!timestamp(x.end)||x.end<x.start)invalid(`transcript[${i}].timestamps`);
  });
  return raw;
}
function setProcessingStage(stage){document.querySelectorAll('.stage').forEach(x=>{const done=['upload','stt','tasks','protocol'].indexOf(x.dataset.stage)<['upload','stt','tasks','protocol'].indexOf(stage);x.classList.toggle('active',x.dataset.stage===stage);x.classList.toggle('done',done);const s=x.querySelector('span');if(s)s.textContent=x.classList.contains('done')?'✓':x.classList.contains('active')?'●':'○'});$('processingHint').textContent='Обработка аудиозаписи может занять несколько минут.'}
function showProcessingError(message){$('processingHint').textContent=message;$('errorTitle').textContent='Не удалось обработать совещание';$('errorMessage').textContent=message;$('errorBox').hidden=false;show('uploadView');$('exportTopBtn').hidden=true}
function sleep(ms){return new Promise(resolve=>setTimeout(resolve,ms))}
async function readApiJson(response){try{return await response.json()}catch{throw new Error('Backend вернул невалидный JSON. Повторите обработку.')}}
async function processMeeting(file){
  if(!file)throw new Error('Выберите аудиофайл для обработки.');
  if(!/\.(mp3|wav|m4a|mp4|webm|ogg)$/i.test(file.name))throw new Error('Неподдерживаемый формат. Выберите MP3, WAV, M4A, MP4, WebM или OGG.');
  if(!file.size)throw new Error('Файл пустой. Выберите другую запись.');
  if(state.audioUrl)URL.revokeObjectURL(state.audioUrl);
  state.fileName=file.name;state.audioUrl=URL.createObjectURL(file);setProcessingStage('stt');
  const form=new FormData();form.append('audio',file,file.name);
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),720000);
  try{
    const response=await fetch(`${API_BASE}/api/meetings/process?async=true`,{method:'POST',body:form,signal:controller.signal});
    if(!response.ok){let detail='Не удалось загрузить файл';try{const body=await response.json();if(typeof body.detail==='string')detail=body.detail}catch{}throw new Error(`${detail} (HTTP ${response.status})`)}
    const queued=await readApiJson(response);
    if(!queued||typeof queued.job_id!=='string'||!queued.job_id)throw new Error('Backend не вернул job_id обработки.');
    for(let attempt=0;attempt<360;attempt++){
      await sleep(attempt===0?500:2000);
      const statusResponse=await fetch(`${API_BASE}/api/meetings/jobs/${encodeURIComponent(queued.job_id)}`,{signal:controller.signal});
      if(!statusResponse.ok)throw new Error(`Не удалось получить статус обработки (HTTP ${statusResponse.status}).`);
      const job=await readApiJson(statusResponse);
      if(job?.status==='queued'||job?.status==='processing'){setProcessingStage('stt');continue}
      if(job?.status==='failed')throw new Error(job.error||'Backend не смог обработать аудиозапись.');
      if(job?.status==='completed')return normalize(job.result);
      throw new Error('Backend вернул неизвестный статус обработки.');
    }
    throw new Error('Обработка превысила 12 минут. Повторите попытку.');
  }catch(error){
    if(error.name==='AbortError')throw new Error('Обработка превысила 12 минут. Повторите попытку.');
    if(error instanceof TypeError)throw new Error('Нет соединения с backend. Проверьте, что API запущен на 127.0.0.1:8000.');
    throw error;
  }finally{clearTimeout(timer)}
}
function show(id){['uploadView','processingView','resultView'].forEach(x=>$(x).hidden=x!==id)}
function start(file){if(workspaceBusy()||!mayLeaveMeeting())return;releaseAudio();state.processing=true;state.data=null;state.historyId=null;state.historyRecord=null;state.historySaved=false;state.historyUnsaved=false;state.audioFile=file;syncWorkspaceNavigation();$('errorBox').hidden=true;$('exportTopBtn').hidden=true;$('processingFile').textContent=file?.name||'Meeting recording';$('processingHint').style.color='';show('processingView');setProcessingStage('stt');return processMeeting(file).then(async data=>{state.data=data;render();show('resultView');$('exportTopBtn').hidden=false;$('pageTitle').textContent='Review protocol';await saveCompletedMeeting(file,data)}).catch(error=>{console.error(error);showProcessingError(error.message||'Unable to process this recording')}).finally(()=>{state.processing=false;syncWorkspaceNavigation()})}
function historyStatus(message,error=false){$('historyStatus').textContent=message;$('historyStatus').dataset.error=String(error)}
function workspaceBusy(){return state.processing||state.restoring||pendingHistoryWrites>0}
function mayLeaveMeeting(){return !state.historyUnsaved||confirm('Последние изменения не сохранены в истории. Перейти к другому совещанию и потерять несохранённые изменения?')}
function syncWorkspaceNavigation(){
  const busy=workspaceBusy();
  ['newMeetingBtn','browseBtn','audioInput'].forEach(id=>$(id).disabled=busy);
  document.querySelectorAll('input[data-field]').forEach(input=>input.disabled=state.restoring);
  document.querySelectorAll('[data-history-id]').forEach(button=>{
    button.disabled=busy;
    const active=button.dataset.historyId===state.historyId;
    button.classList.toggle('active',active);
    button.setAttribute('aria-current',active?'true':'false');
  });
}
async function refreshHistory(){
  try{
    const records=await MeetingHistory.list();
    $('historyList').innerHTML=records.length?records.map(record=>{
      const date=new Date(record.createdAt).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});
      return `<button type="button" class="history-entry" data-history-id="${esc(record.id)}" title="${esc(record.fileName)}"><strong>${esc(record.fileName)}</strong><small>${esc(date)}</small></button>`;
    }).join(''):'<p class="history-empty">Обработанные совещания появятся здесь.</p>';
    syncWorkspaceNavigation();
    return true;
  }catch(error){historyStatus('История недоступна. Разрешите хранение данных сайта в браузере.',true);return false}
}
function queueHistoryWrite(write,successMessage){
  pendingHistoryWrites++;state.historyUnsaved=true;syncWorkspaceNavigation();
  historyStatus('Сохранение… Не закрывайте страницу.');
  // Capture each result before enqueueing: navigating must not overwrite another meeting.
  historyWrites=historyWrites.then(write).then(()=>{
    state.historySaved=true;
    if(pendingHistoryWrites===1){state.historyUnsaved=false;historyStatus(successMessage)}
  }).catch(error=>{
    historyStatus('Не удалось сохранить историю. Проверьте свободное место и разрешения браузера. Текущий результат доступен до закрытия страницы.',true);
  }).finally(()=>{pendingHistoryWrites--;syncWorkspaceNavigation()});
  return historyWrites;
}
async function saveCompletedMeeting(file,data){
  try{
    const now=new Date().toISOString();
    const record={id:crypto.randomUUID(),fileName:file.name,createdAt:now,updatedAt:now,data:JSON.parse(JSON.stringify(data)),audio:file};
    state.historyId=record.id;state.historyRecord=record;
    await queueHistoryWrite(()=>MeetingHistory.save(record),'Протокол и аудио сохранены.');
    await refreshHistory();
  }catch(error){state.historyUnsaved=true;historyStatus('Не удалось сохранить историю. Текущий результат доступен до закрытия страницы.',true)}
}
function saveHistoryEdits(){
  if(!state.historyId||!state.data)return;
  const record={...state.historyRecord,data:JSON.parse(JSON.stringify(state.data)),updatedAt:new Date().toISOString()};
  const needsListing=!state.historySaved;
  const pending=queueHistoryWrite(()=>state.historySaved?MeetingHistory.updateData(record.id,record.data,record.updatedAt):MeetingHistory.save(record),'Изменения сохранены.');
  return needsListing?pending.then(refreshHistory):pending;
}
function releaseAudio(){
  const player=$('audioPlayer');
  player.pause();player.removeAttribute('src');player.load();
  if(state.audioUrl)URL.revokeObjectURL(state.audioUrl);
  state.audioUrl=null;state.audioFile=null;
}
async function openHistory(id){
  if(workspaceBusy()||!mayLeaveMeeting())return;
  state.restoring=true;syncWorkspaceNavigation();
  historyStatus('Открываем сохранённое совещание…');
  try{
    await historyWrites;
    const record=await MeetingHistory.get(id);
    if(!record)throw new Error('Совещание не найдено в истории.');
    const data=normalize(record.data);
    if(!(record.audio instanceof Blob)||!record.audio.size)throw new Error('В истории отсутствует исходное аудио.');
    const audioUrl=URL.createObjectURL(record.audio);
    releaseAudio();
    state.data=data;state.fileName=record.fileName;state.historyId=id;state.historyRecord=record;state.historySaved=true;state.historyUnsaved=false;state.audioFile=record.audio;state.audioUrl=audioUrl;
    $('errorBox').hidden=true;render();show('resultView');$('exportTopBtn').hidden=false;$('pageTitle').textContent='Review protocol';
    $('nowPlaying').textContent='Select a timestamp or “Play evidence” to hear the source phrase.';
    historyStatus('Загружено из истории — без повторной обработки.');
  }catch(error){historyStatus(error.message||'Не удалось открыть совещание из истории.',true)}
  finally{state.restoring=false;syncWorkspaceNavigation()}
}
function newMeeting(){
  if(workspaceBusy()||!mayLeaveMeeting())return;
  releaseAudio();state.data=null;state.historyId=null;state.historyRecord=null;state.historySaved=false;state.historyUnsaved=false;state.fileName='';
  $('audioInput').value='';$('errorBox').hidden=true;
  show('uploadView');$('exportTopBtn').hidden=true;$('pageTitle').textContent='Upload a meeting';syncWorkspaceNavigation();
}
function render(){const d=state.data,m=d.meeting,s=d.summary; $('meetingTitle').textContent=m.title||'Meeting protocol';$('meetingDate').textContent=m.date||'Date not set';$('meetingDuration').textContent=fmtTime(m.duration_seconds);$('meetingLanguage').textContent=m.language||'Language not set';$('summaryText').textContent=s.executive_summary||'';$('topicList').innerHTML=(s.key_topics||[]).map(x=>`<span class="topic">${esc(x)}</span>`).join('');$('decisionCount').textContent=(s.decisions||[]).length;$('taskCount').textContent=d.action_items.length;$('segmentCount').textContent=d.transcript.length;const avg=d.action_items.reduce((a,x)=>a+(Number(x.confidence)||0),0)/(d.action_items.length||1);$('confidenceValue').textContent=`${Math.round(avg*100)}%`;const decisionText=x=>typeof x==='string'?x:(x&& (x.decision||x.text||''));$('decisionList').innerHTML=(s.decisions||[]).map(x=>`<div class="decision">${esc(decisionText(x))}<div class="decision-meta"><span>Captured from protocol</span></div></div>`).join('');$('taskList').innerHTML=d.action_items.map((x,i)=>taskHtml(x,i)).join('');$('transcriptList').innerHTML=d.transcript.map((x,i)=>`<div class="transcript-line" data-start="${x.start}" data-index="${i}"><div class="timestamp">${fmtTime(x.start)}</div><div><div class="speaker">${esc(x.speaker_name??x.speaker_id??x.speaker??'SPEAKER')}</div><div class="transcript-text">${esc(x.text)}</div></div></div>`).join('');document.querySelectorAll('.transcript-line').forEach(x=>x.addEventListener('click',()=>seek(Number(x.dataset.start),x.dataset.index)));if(state.audioUrl)$('audioPlayer').src=state.audioUrl}
function taskHtml(x,i){return `<article class="task-card"><div class="task-header"><div class="task-title"><input class="task-title-input" aria-label="Поручение ${i+1}" data-field="task" data-index="${i}" value="${esc(x.task)}" /></div><span class="confidence">${Math.round((x.confidence||0)*100)}% confidence</span></div><div class="task-fields"><label class="field-chip editable"><b>ASSIGNEE</b><input data-field="assignee" data-index="${i}" value="${esc(x.assignee??x.assignee_speaker_id??'')}" placeholder="Не определён" /></label><label class="field-chip editable"><b>DEADLINE</b><input data-field="deadline" data-index="${i}" value="${esc(x.deadline??'')}" placeholder="Срок не указан" /></label><span class="field-chip"><b>SPEAKER</b>${esc(x.speaker_name??x.speaker??'—')}</span></div><div class="task-footer"><div class="source-quote">“${esc(x.source_quote||x.task)}” · ${fmtTime(x.timestamp_start)}–${fmtTime(x.timestamp_end)}</div><div><button class="save-btn" data-save="${i}">Сохранить</button> <button class="evidence-btn" data-evidence="${x.timestamp_start}" data-index="${i}">▶ Play evidence</button></div></div></article>`}
function seek(time,index){const p=$('audioPlayer');if(p.src){p.currentTime=time;p.play().catch(()=>{})}const segment=state.data.transcript.findIndex(x=>Number(x.start)<=time&&Number(x.end)>=time);const active=segment>=0?segment:index;$('nowPlaying').textContent=`Playing evidence at ${fmtTime(time)} — ${state.data.transcript[active]?.text||'source phrase'}`;document.querySelectorAll('.transcript-line').forEach(x=>x.classList.toggle('active',x.dataset.index==active))}
document.addEventListener('input',e=>{if(e.target.matches('input[data-field]')&&state.data&&!state.restoring){state.data.action_items[Number(e.target.dataset.index)][e.target.dataset.field]=e.target.value;saveHistoryEdits()}});document.addEventListener('click',e=>{const b=e.target.closest('[data-evidence]');if(b)seek(Number(b.dataset.evidence),Number(b.dataset.index));const saved=e.target.closest('[data-history-id]');if(saved)openHistory(saved.dataset.historyId)});
document.addEventListener('click',async e=>{const b=e.target.closest('[data-save]');if(b&&state.data&&!state.restoring){const item=state.data.action_items[Number(b.dataset.save)];b.closest('.task-card').querySelectorAll('input[data-field]').forEach(input=>{if(['task','assignee','deadline'].includes(input.dataset.field))item[input.dataset.field]=input.value});b.disabled=true;try{await saveHistoryEdits();b.textContent=state.historyUnsaved?'Не сохранено':'Сохранено'}finally{b.disabled=false}}});
globalThis.addEventListener('beforeunload',event=>{if(pendingHistoryWrites||state.historyUnsaved){event.preventDefault();event.returnValue=''}});
function xml(s){return esc(s)}
function crc32(bytes){let table=crc32.table;if(!table){table=crc32.table=[];for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;table[n]=c>>>0}}let c=0xffffffff;for(const b of bytes)c=table[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0}
function u16(n){return new Uint8Array([n&255,(n>>>8)&255])}function u32(n){return new Uint8Array([n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255])}function cat(...arrs){const n=arrs.reduce((a,x)=>a+x.length,0),o=new Uint8Array(n);let p=0;arrs.forEach(x=>{o.set(x,p);p+=x.length});return o}
function zip(files){const enc=new TextEncoder(),local=[],central=[];let offset=0;files.forEach(([name,content])=>{const nb=enc.encode(name),data=enc.encode(content),crc=crc32(data);const localHeader=cat(new Uint8Array([80,75,3,4]),u16(20),u16(0),u16(0),u16(0),u16(0),u32(crc),u32(data.length),u32(data.length),u16(nb.length),u16(0));const head=cat(localHeader,nb,data);local.push(head);const centralHeader=cat(new Uint8Array([80,75,1,2]),u16(20),u16(20),u16(0),u16(0),u16(0),u16(0),u32(crc),u32(data.length),u32(data.length),u16(nb.length),u16(0),u16(0),u16(0),u16(0),u32(0),u32(offset));central.push(cat(centralHeader,nb));offset+=head.length});const body=cat(...local),cd=cat(...central),eoc=cat(new Uint8Array([80,75,5,6]),u16(0),u16(0),u16(files.length),u16(files.length),u32(cd.length),u32(body.length),u16(0));return cat(body,cd,eoc)}
function paragraph(text,style='Normal'){return `<w:p><w:pPr><w:pStyle w:val="${style}"/></w:pPr><w:r><w:t xml:space="preserve">${xml(text)}</w:t></w:r></w:p>`}
function showExportError(message){$('errorTitle').textContent='Не удалось скачать DOCX';$('errorMessage').textContent=message;$('errorBox').hidden=false}
function docxCell(text){return `<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>${paragraph(text)}</w:tc>`}
function docxTable(items){const header=['№','Поручение','Ответственный','Срок'];const rows=[`<w:tr>${header.map(docxCell).join('')}</w:tr>`];items.forEach((x,i)=>rows.push(`<w:tr>${[String(i+1),x.task||'—',x.assignee||'—',x.deadline||'—'].map(docxCell).join('')}</w:tr>`));return `<w:tbl><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr>${rows.join('')}</w:tbl>`}
function exportDocx(){console.log('[DOCX] export handler started');try{const d=state.data,m=d?.meeting,s=d?.summary;if(!m||!s||!Array.isArray(d.action_items))throw new Error('Текущий результат совещания отсутствует или повреждён');console.log('[DOCX] meeting data exists',Boolean(m),'action_items count',d.action_items.length);let body=paragraph('ПРОТОКОЛ СОВЕЩАНИЯ','Title')+paragraph(m.title||'Совещание','Heading1')+paragraph(`Дата: ${m.date||'—'}    Длительность: ${fmtTime(m.duration_seconds)}    Язык: ${m.language||'—'}`)+paragraph('Краткое содержание','Heading1')+paragraph(s.executive_summary||'')+paragraph('Принятые решения','Heading1')+(s.decisions||[]).map(x=>paragraph(typeof x==='string'?x:x.decision||x.text||'—')).join('')+paragraph('Поручения','Heading1')+docxTable(d.action_items);const sources=d.action_items.map((x,i)=>paragraph(`${i+1}. Источник (${fmtTime(x.timestamp_start)}): ${x.source_quote||x.task}`)).join('');body+=paragraph('Источники','Heading1')+sources;const files=[['[Content_Types].xml','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'],['_rels/.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'],['word/document.xml',`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body}<w:sectPr/></w:body></w:document>`]];console.log('[DOCX] doc generation completed');const bytes=zip(files),blob=new Blob([bytes],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'});console.log('[DOCX] blob size',blob.size);const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='meeting-protocol.docx';a.click();console.log('[DOCX] download triggered');setTimeout(()=>URL.revokeObjectURL(a.href),1000)}catch(error){console.error('[DOCX] export failed',error);showExportError(error.message||'Неизвестная ошибка экспорта')}}
document.addEventListener('DOMContentLoaded',()=>{const input=$('audioInput');$('dismissError').addEventListener('click',()=>{$('errorBox').hidden=true});$('browseBtn').addEventListener('click',e=>{e.preventDefault();e.stopPropagation();input.click()});input.addEventListener('change',e=>e.target.files[0]&&start(e.target.files[0]));$('newMeetingBtn').addEventListener('click',newMeeting);['exportBtn','exportTopBtn'].forEach(id=>$(id).addEventListener('click',exportDocx));const dz=$('dropzone');['dragenter','dragover'].forEach(x=>dz.addEventListener(x,e=>{e.preventDefault();dz.classList.add('dragging')}));['dragleave','drop'].forEach(x=>dz.addEventListener(x,e=>{e.preventDefault();dz.classList.remove('dragging')}));dz.addEventListener('drop',e=>e.dataTransfer.files[0]&&start(e.dataTransfer.files[0]));refreshHistory().then(ok=>{if(ok)historyStatus('История хранится только в этом браузере.')})});
