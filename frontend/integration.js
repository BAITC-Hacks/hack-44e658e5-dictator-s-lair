/* Production adapter and resilience layer. The AI/STT pipeline stays behind this boundary. */
const USE_MOCK = false;
const PROCESS_TIMEOUT_MS = 720000;
const API_BASE = 'http://127.0.0.1:8000';

function showError(title, message) {
  $('errorTitle').textContent = title;
  $('errorMessage').textContent = message;
  $('errorBox').hidden = false;
}

function clearError() { $('errorBox').hidden = true; }

function setProcessingStage(stage) {
  const order = ['upload', 'stt', 'tasks', 'protocol'];
  document.querySelectorAll('.stage').forEach(row => {
    const index = order.indexOf(row.dataset.stage);
    const active = row.dataset.stage === stage;
    row.classList.toggle('active', active);
    row.classList.toggle('done', index < order.indexOf(stage));
    row.querySelector('span').textContent = index < order.indexOf(stage) ? '✓' : active ? '●' : '○';
  });
}

function validateResponse(raw) {
  if (!raw || typeof raw !== 'object') throw new Error('Backend вернул пустой или некорректный JSON.');
  for (const field of ['meeting', 'summary', 'action_items', 'transcript']) {
    if (!(field in raw)) throw new Error(`В ответе backend отсутствует обязательное поле «${field}».`);
  }
  if (!raw.meeting || typeof raw.meeting !== 'object') throw new Error('Поле meeting имеет неверный формат.');
  if (!raw.summary || typeof raw.summary !== 'object') throw new Error('Поле summary имеет неверный формат.');
  if (!Array.isArray(raw.action_items) || !Array.isArray(raw.transcript)) throw new Error('action_items и transcript должны быть массивами.');
  return raw;
}

function toSeconds(value) {
  if (typeof value === 'string' && value.includes(':')) {
    const parts = value.split(':').map(Number);
    return parts.length === 2 ? parts[0] * 60 + parts[1] : parts[0] * 3600 + parts[1] * 60 + parts[2];
  }
  return Number(value ?? 0) || 0;
}

function normalize(raw) {
  const d = clone(MOCK);
  validateResponse(raw);
  d.meeting = {...d.meeting, ...raw.meeting};
  d.summary = {...d.summary, ...raw.summary};
  d.summary.key_topics = Array.isArray(raw.summary.key_topics) ? raw.summary.key_topics : [];
  d.summary.decisions = Array.isArray(raw.summary.decisions) ? raw.summary.decisions.map(x => typeof x === 'string' ? x : x.decision || x.text || 'Решение без описания') : [];
  d.action_items = raw.action_items.map((x, i) => ({
    id: x.id || `task-${i + 1}`, task: x.task || 'Поручение без описания', assignee: x.assignee ?? null,
    deadline: x.deadline ?? null, speaker: x.speaker ?? null, timestamp_start: toSeconds(x.timestamp_start ?? x.start ?? 0),
    timestamp_end: toSeconds(x.timestamp_end ?? x.end ?? x.timestamp_start ?? x.start ?? 0), source_quote: x.source_quote || x.task || '', confidence: Number.isFinite(Number(x.confidence)) ? Number(x.confidence) : null
  }));
  d.transcript = raw.transcript.map(x => ({speaker: x.speaker ?? null, start: toSeconds(x.start ?? 0), end: toSeconds(x.end ?? x.start ?? 0), text: x.text || ''}));
  return d;
}

function mockProcessMeeting() {
  return new Promise(resolve => {
    setProcessingStage('stt');
    setTimeout(() => { setProcessingStage('tasks'); setTimeout(() => { setProcessingStage('protocol'); setTimeout(() => resolve(normalize(MOCK)), 250); }, 350); }, 450);
  });
}

async function processMeeting(file) {
  if (USE_MOCK) return mockProcessMeeting();
  if (!file) throw new Error('Выберите аудиофайл перед обработкой.');
  if (!file.type.startsWith('audio/') && !/\.(mp3|wav|m4a|ogg|webm)$/i.test(file.name)) throw new Error('Поддерживаются MP3, WAV, M4A, OGG и WebM.');
  if (!file.size) throw new Error('Файл пустой. Выберите другую запись.');
  setProcessingStage('stt');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROCESS_TIMEOUT_MS);
  try {
    const form = new FormData(); form.append('audio', file, file.name);
    const response = await fetch(`${API_BASE}/api/meetings/process?async=true`, {method:'POST', body:form, signal:controller.signal});
    if (!response.ok) throw new Error(response.status >= 500 ? 'Backend временно недоступен (ошибка сервера).' : `Backend отклонил файл (HTTP ${response.status}).`);
    let queued; try { queued = await response.json(); } catch { throw new Error('Backend вернул невалидный JSON.'); }
    if (!queued.job_id) throw new Error('Backend не вернул идентификатор обработки.');
    for (let attempt = 0; attempt < 360; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, attempt === 0 ? 500 : 2000));
      const statusResponse = await fetch(`${API_BASE}/api/meetings/jobs/${encodeURIComponent(queued.job_id)}`, {signal:controller.signal});
      if (!statusResponse.ok) throw new Error(`Не удалось получить статус обработки (HTTP ${statusResponse.status}).`);
      let job; try { job = await statusResponse.json(); } catch { throw new Error('Backend вернул невалидный статус обработки.'); }
      if (job.status === 'queued' || job.status === 'processing') { setProcessingStage(job.status === 'queued' ? 'stt' : 'tasks'); continue; }
      if (job.status === 'failed') throw new Error(job.error || 'Backend не смог обработать аудиозапись.');
      if (job.status === 'completed') { setProcessingStage('protocol'); return normalize(job.result); }
      throw new Error(`Неизвестный статус обработки: ${job.status || 'пустой'}.`);
    }
    throw new Error('Обработка превысила 12 минут. Попробуйте более короткую запись.');
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Обработка превысила 12 минут. Попробуйте более короткую запись.');
    if (error instanceof TypeError) throw new Error('Backend недоступен. Проверьте, запущен ли API на 127.0.0.1:8000.');
    throw error;
  } finally { clearTimeout(timer); }
}

function confidenceMeta(value) {
  if (value == null || Number.isNaN(value)) return {text:'Не указана', cls:'', note:''};
  const pct = Math.round(value * 100);
  if (value < .6) return {text:`${pct}% · Низкая`, cls:'confidence-low', note:'Рекомендуется проверить исходный фрагмент'};
  if (value < .8) return {text:`${pct}% · Средняя`, cls:'', note:''};
  return {text:`${pct}% · Высокая`, cls:'', note:''};
}

function taskHtml(x, i) {
  const c = confidenceMeta(x.confidence);
  return `<article class="task-card"><div class="task-header"><div class="task-title"><input class="task-title-input" data-field="task" data-index="${i}" value="${esc(x.task)}" /></div><span class="confidence ${c.cls}">${c.text}</span></div>${c.note ? `<span class="confidence-note">${c.note}</span>` : ''}<div class="task-fields"><label class="field-chip editable"><b>ASSIGNEE</b><input data-field="assignee" data-index="${i}" value="${esc(x.assignee ?? 'Не определён')}" /></label><label class="field-chip editable"><b>DEADLINE</b><input data-field="deadline" data-index="${i}" value="${esc(x.deadline ?? 'Срок не указан')}" /></label><span class="field-chip"><b>SPEAKER</b>${esc(x.speaker ?? '—')}</span></div><div class="task-footer"><div class="source-quote">“${esc(x.source_quote || x.task)}” · ${fmtTime(x.timestamp_start)}–${fmtTime(x.timestamp_end)}</div><div><button class="save-btn" data-save="${i}">Save</button> <button class="evidence-btn" data-evidence="${x.timestamp_start}" data-index="${i}">▶ Проверить в аудио</button></div></div></article>`;
}

function render() {
  const d = state.data, m = d.meeting || {}, s = d.summary || {};
  $('meetingTitle').textContent = m.title || 'Meeting protocol'; $('meetingDate').textContent = m.date || 'Date not set';
  $('meetingDuration').textContent = fmtTime(m.duration_seconds); $('meetingLanguage').textContent = m.language || 'Language not set';
  $('summaryText').textContent = s.executive_summary || 'Саммари не предоставлено.';
  $('topicList').innerHTML = (s.key_topics || []).map(x => `<span class="topic">${esc(x)}</span>`).join('') || '<span class="panel-note">Темы не указаны</span>';
  $('decisionCount').textContent = (s.decisions || []).length; $('taskCount').textContent = d.action_items.length; $('segmentCount').textContent = d.transcript.length;
  const withConfidence = d.action_items.filter(x => x.confidence != null), avg = withConfidence.reduce((a,x)=>a+x.confidence,0)/(withConfidence.length||1);
  $('confidenceValue').textContent = withConfidence.length ? `${Math.round(avg*100)}%` : '—';
  $('decisionList').innerHTML = (s.decisions || []).map(x => `<div class="decision">${esc(x)}<div class="decision-meta"><span>Captured from protocol</span></div></div>`).join('') || '<div class="panel-note">Решения не найдены</div>';
  $('taskList').innerHTML = d.action_items.length ? d.action_items.map(taskHtml).join('') : '<div class="panel-note">Поручения не найдены</div>';
  $('transcriptList').innerHTML = d.transcript.map((x,i) => `<div class="transcript-line" data-start="${x.start}" data-index="${i}"><div class="timestamp">${fmtTime(x.start)}</div><div><div class="speaker">${esc(x.speaker || 'SPEAKER')}</div><div class="transcript-text">${esc(x.text)}</div></div></div>`).join('') || '<div class="panel-note">Транскрипт пуст</div>';
  document.querySelectorAll('.transcript-line').forEach(x => x.addEventListener('click', () => seek(Number(x.dataset.start), Number(x.dataset.index))));
  if (state.audioUrl) $('audioPlayer').src = state.audioUrl;
}

async function start(file) {
  clearError();
  if (state.audioUrl) { URL.revokeObjectURL(state.audioUrl); state.audioUrl = null; }
  const isDemo = !file;
  if (file) { state.fileName = file.name; state.audioUrl = URL.createObjectURL(file); }
  $('processingFile').textContent = file?.name || 'Sample meeting · local demo'; show('processingView');
  try { const data = isDemo ? await mockProcessMeeting() : await processMeeting(file); state.data = data; render(); show('resultView'); $('exportTopBtn').hidden = false; $('pageTitle').textContent = 'Review protocol'; }
  catch (error) { show('uploadView'); showError('Не удалось обработать совещание', error.message || 'Неизвестная ошибка.'); }
}

function zip(files) {
  const enc = new TextEncoder(), locals = [], centrals = []; let offset = 0;
  const put16 = (v,n,p) => new DataView(v.buffer).setUint16(p,n,true); const put32 = (v,n,p) => new DataView(v.buffer).setUint32(p,n,true);
  files.forEach(([name, text]) => { const nb=enc.encode(name), data=enc.encode(text), crc=crc32(data), local=new Uint8Array(30+nb.length); put32(local,0x04034b50,0); put16(local,20,4); put32(local,crc,14); put32(local,data.length,18); put32(local,data.length,22); put16(local,nb.length,26); local.set(nb,30); locals.push(cat(local,data)); const central=new Uint8Array(46+nb.length); put32(central,0x02014b50,0); put16(central,20,4); put16(central,20,6); put32(central,crc,16); put32(central,data.length,20); put32(central,data.length,24); put16(central,nb.length,28); put32(central,offset,42); central.set(nb,46); centrals.push(central); offset+=local.length+data.length; });
  const body=cat(...locals), directory=cat(...centrals), end=new Uint8Array(22); put32(end,0x06054b50,0); put16(end,files.length,8); put16(end,files.length,10); put32(end,directory.length,12); put32(end,body.length,16); return cat(body,directory,end);
}

function exportDocx() {
  const d=state.data,m=d.meeting||{},s=d.summary||{}; let body=paragraph('ПРОТОКОЛ СОВЕЩАНИЯ','Title')+paragraph(m.title||'Совещание','Heading1')+paragraph(`Дата: ${m.date||'—'}    Длительность: ${fmtTime(m.duration_seconds)}    Язык: ${m.language||'—'}`)+paragraph('Саммари','Heading1')+paragraph(s.executive_summary||'');
  body+=paragraph('Ключевые темы','Heading1')+(s.key_topics||[]).map(x=>paragraph('• '+x)).join('')+paragraph('Решения','Heading1')+(s.decisions||[]).map(x=>paragraph('• '+x)).join('')+paragraph('Поручения','Heading1');
  d.action_items.forEach((x,i)=>{body+=paragraph(`${i+1}. ${x.task}`)+paragraph(`Ответственный: ${x.assignee??'Не определён'} | Срок: ${x.deadline??'Срок не указан'} | Уверенность: ${x.confidence==null?'Не указана':Math.round(x.confidence*100)+'%'}`)+paragraph(`Источник (${fmtTime(x.timestamp_start)}): ${x.source_quote||x.task}`)});
  body+=paragraph('Приложение: транскрипт','Heading1'); d.transcript.forEach(x=>body+=paragraph(`[${fmtTime(x.start)}] ${x.speaker||'SPEAKER'}: ${x.text}`));
  const files=[['[Content_Types].xml','<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'],['_rels/.rels','<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'],['word/document.xml',`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body}<w:sectPr/></w:body></w:document>`]];
  const blob=new Blob([zip(files)],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}),a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='meeting-protocol.docx'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}

document.addEventListener('click', e => { const save=e.target.closest('[data-save]'); if(save){ render(); } if(e.target.id==='dismissError') clearError(); });
document.addEventListener('click', e => { if(e.target.closest('#exportBtn,#exportTopBtn')) { e.preventDefault(); e.stopImmediatePropagation(); exportDocx(); } }, true);
