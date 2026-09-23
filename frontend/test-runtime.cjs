const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
const adapterSource = fs.readFileSync(path.join(__dirname, 'integration.js'), 'utf8');
const htmlSource = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');

// Test-only fixtures. The production page must always start without meeting data.
function resultFixture() {
  return {
    meeting: {title: 'Real response fixture', date: null, duration_seconds: 40, language: 'ru', participants: []},
    summary: {executive_summary: 'Short summary', key_topics: ['Budget'], decisions: [{decision: 'Approved', source_quote: 'Approve'}]},
    action_items: [{id: 'a1', task: 'Prepare report', assignee: null, assignee_speaker_id: null, deadline: null,
      speaker: 'SPEAKER_01', speaker_name: 'Named chair', timestamp_start: 12, timestamp_end: 18,
      source_quote: 'Prepare report', confidence: 0.8}],
    transcript: [
      {speaker: 'SPEAKER_01', speaker_id: 'SPEAKER_01', speaker_name: 'Named chair', speaker_name_confidence: 0.9,
        speaker_name_evidence: [{source_quote: 'I am the chair'}], start: 0, end: 11, text: 'Opening'},
      {speaker: 'SPEAKER_02', speaker_id: 'SPEAKER_02', speaker_name: null, start: 12, end: 18, text: 'Prepare report'},
    ],
  };
}

function historyStore() {
  const records = new Map();
  return {
    records,
    async list() { return [...records.values()].map(({id, fileName, createdAt, updatedAt}) => ({id, fileName, createdAt, updatedAt})); },
    async save(record) { records.set(record.id, structuredClone(record)); },
    async get(id) { return records.has(id) ? structuredClone(records.get(id)) : null; },
    async updateData(id, data, updatedAt) {
      if (!records.has(id)) throw new Error('Meeting not found');
      const record = records.get(id);
      records.set(id, {...record, data: structuredClone(data), updatedAt});
    },
  };
}

function harness(responses = [], history = historyStore()) {
  const elements = new Map();
  const events = new Map();
  const requests = [];
  const objectUrls = [];
  const revokedUrls = [];
  const downloads = [];
  function element(id) {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, {
        id, textContent: '', innerHTML: '', hidden: false, style: {}, dataset: {}, src: '', currentTime: 0,
        listeners: new Map(),
        classList: {toggle(name, on) { on ? classes.add(name) : classes.delete(name); }, contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name)},
        addEventListener(type, handler) { this.listeners.set(type, [...(this.listeners.get(type) || []), handler]); },
        click() { this.clickCalls = (this.clickCalls || 0) + 1; },
        play() { this.playCalls = (this.playCalls || 0) + 1; return Promise.resolve(); },
        pause() { this.pauseCalls = (this.pauseCalls || 0) + 1; },
        load() {},
        removeAttribute(name) { if (name === 'src') this.src = ''; },
        setAttribute(name, value) { this[name] = value; },
      });
    }
    return elements.get(id);
  }
  const context = vm.createContext({
    AbortController,
    TypeError,
    TextEncoder,
    addEventListener(type, handler) { events.set(type, [...(events.get(type) || []), handler]); },
    confirm() { return false; },
    Blob,
    crypto: require('node:crypto').webcrypto,
    MeetingHistory: history,
    console: {log() {}, error() {}},
    document: {
      getElementById: element,
      createElement(tag) { const node = element(`created-${downloads.length}`); if (tag === 'a') downloads.push(node); return node; },
      querySelectorAll() { return []; },
      addEventListener(type, handler) { events.set(type, [...(events.get(type) || []), handler]); },
    },
    FormData: class { constructor() { this.fields = []; } append(...args) { this.fields.push(args); } },
    URL: {
      createObjectURL(file) { objectUrls.push(file); return `blob:local-${objectUrls.length}`; },
      revokeObjectURL(url) { revokedUrls.push(url); },
    },
    setTimeout(fn, ms) { if (ms < 10000) queueMicrotask(fn); return 1; },
    clearTimeout() {},
    fetch: async (url, options = {}) => {
      requests.push({url, options});
      const response = responses.shift();
      if (response instanceof Error) throw response;
      if (!response) throw new Error('Unexpected request');
      return {ok: true, status: 200, json: async () => response.body, ...response};
    },
  });
  vm.runInContext(appSource, context, {filename: 'app.js'});
  return {context, element, events, requests, history, objectUrls, revokedUrls, downloads, evaluate: code => vm.runInContext(code, context)};
}

const audioFile = () => ({name: 'meeting.mp3', type: 'audio/mpeg', size: 1234});
const audioBlob = () => Object.assign(new Blob(['test-only audio bytes'], {type: 'audio/mpeg'}), {name: 'meeting.mp3'});
const settle = () => new Promise(resolve => setImmediate(resolve));
function edit(h, field, value) {
  h.events.get('input').forEach(handler => handler({target: {
    matches: selector => selector === 'input[data-field],textarea[data-field]', dataset: {field, index: '0'}, value,
  }}));
}

test('the shipped page loads local history before app.js and has no runtime mock/demo path', () => {
  assert.deepEqual([...htmlSource.matchAll(/<script\s+src="([^"]+)"/g)].map(x => x[1]), ['./history.js', './app.js']);
  for (const source of [appSource, adapterSource, htmlSource]) {
    assert.doesNotMatch(source, /\bMOCK\b|\bUSE_MOCK\b|\bstartDemo\b|demoBtn|Load demo/i);
  }
  const h = harness();
  assert.equal(h.evaluate('state.data'), null);
});

test('contract normalization preserves identity and evidence extensions', () => {
  const h = harness();
  const result = resultFixture();
  assert.equal(h.context.normalize(result), result);
  assert.deepEqual(h.context.normalize(result).transcript[0].speaker_name_evidence, result.transcript[0].speaker_name_evidence);
  for (const malformed of [null, {}, {meeting: {}, summary: {}, action_items: {}, transcript: []}]) {
    assert.throws(() => h.context.normalize(malformed), /Сервер вернул некорректные данные совещания/);
  }
});

function businessTopic() {
  const evidence={source_quote:'Готовность 23% от плана.',timestamp_start:2,timestamp_end:8,source_segment_indices:[0]};
  return {title:'Цифровизация',facts:[{metric:'Готовность',value:'23%',context:evidence.source_quote,...evidence}],problems:[],risks:[],action_item_ids:['a1']};
}

test('business topics are optional for previously saved protocols', () => {
  const h=harness();h.context.result=resultFixture();h.evaluate('state.data=normalize(result);render()');
  assert.equal(h.element('factsPanel').hidden,true);
  assert.equal(h.element('topicCount').textContent,'—');
});

test('business facts render actual structured data and escape transcript markup', () => {
  const h=harness(),result=resultFixture(),topic=businessTopic();
  topic.title='<script>bad</script>';topic.facts[0].context='<img src=x> 23% от плана.';
  result.summary.topics=[topic];h.context.result=result;h.evaluate('state.data=normalize(result);render()');
  assert.equal(h.element('factsPanel').hidden,false);
  assert.equal(h.element('topicCount').textContent,1);
  assert.match(h.element('factsBody').innerHTML,/23%/);
  assert.match(h.element('factsBody').innerHTML,/&lt;script&gt;/);
  assert.doesNotMatch(h.element('factsBody').innerHTML,/<script>|<img/);
  assert.match(h.element('factsBody').innerHTML,/00:02/);
});

test('malformed topic evidence is rejected rather than manufactured by frontend', () => {
  const h=harness();
  for(const modify of [d=>d.summary.topics={},d=>d.summary.topics[0].facts=null,d=>d.summary.topics[0].facts[0].timestamp_start=-1,d=>d.summary.topics[0].facts[0].context={}]){
    const result=resultFixture();result.summary.topics=[businessTopic()];modify(result);
    assert.throws(()=>h.context.normalize(result),/summary.topics/);
  }
});

test('zero decisions has a visible honest empty state', () => {
  const h=harness();h.context.result=resultFixture();h.context.result.summary.decisions=[];
  h.evaluate('state.data=result;render()');
  assert.match(h.element('decisionList').innerHTML,/Явные принятые решения не выделены/);
});

test('nested malformed payloads are rejected with a clear error before rendering', () => {
  const h = harness();
  const changes = [
    d => { d.meeting = []; },
    d => { d.summary = 'invalid'; },
    d => { d.summary.key_topics = {}; },
    d => { d.summary.key_topics = [null]; },
    d => { d.summary.decisions = {}; },
    d => { d.summary.decisions = [null]; },
    d => { d.summary.decisions = [{decision: {}}]; },
    d => { d.action_items = [null]; },
    d => { d.action_items = ['not an action']; },
    d => { d.action_items = [[]]; },
    d => { d.action_items[0].task = {}; },
    d => { d.action_items[0].source_quote = []; },
    d => { d.transcript = [null]; },
    d => { d.transcript = ['not a segment']; },
    d => { d.transcript = [[]]; },
    d => { d.transcript[0].text = {}; },
  ];
  for (const value of [null, undefined, '12', '" onclick="alert(1)', -1, Infinity, NaN]) {
    for (const field of ['timestamp_start', 'timestamp_end']) {
      changes.push(d => { d.action_items[0][field] = value; });
    }
    for (const field of ['start', 'end']) {
      changes.push(d => { d.transcript[0][field] = value; });
    }
  }
  changes.push(d => { d.action_items[0].timestamp_end = 1; });
  changes.push(d => { d.transcript[1].end = 1; });
  for (const change of changes) {
    const result = resultFixture();
    change(result);
    assert.throws(() => h.context.normalize(result), /некорректные данные совещания.*Повторите обработку/);
  }
  const valid = resultFixture();
  valid.summary.decisions = ['Approved', {text: 'Deferred', source_quote: 'Next week'}];
  assert.equal(h.context.normalize(valid), valid);
});

test('render prefers speaker names, falls back to IDs, and never guesses task owner from talker', () => {
  const h = harness();
  h.context.fixture = resultFixture();
  const original = structuredClone(h.context.fixture);
  h.evaluate('state.data = fixture; render()');
  assert.match(h.element('transcriptList').innerHTML, /Named chair/);
  assert.match(h.element('transcriptList').innerHTML, /Спикер 02/);
  assert.equal(h.context.fixture.transcript[1].speaker_id, 'SPEAKER_02', 'display localization must not mutate IDs');
  assert.match(h.element('taskList').innerHTML, /data-field="assignee"[^>]*value=""[^>]*placeholder="Не определён"/);
  assert.match(h.element('decisionList').innerHTML, /Approved/);
  const task = {...resultFixture().action_items[0], assignee_speaker_id: 'SPEAKER_02'};
  assert.match(h.context.taskHtml(task, 0), /data-field="assignee"[^>]*value="SPEAKER_02"/);
  task.assignee = 'Named owner';
  assert.match(h.context.taskHtml(task, 0), /data-field="assignee"[^>]*value="Named owner"/);
  assert.deepEqual(h.context.fixture, original, 'Russian labels and layout must not rewrite backend data');
});

test('real API path uploads audio and polls queued/processing/completed without substituting result', async () => {
  const result = resultFixture();
  const h = harness([{body: {job_id: 'job/123'}}, {body: {status: 'queued'}}, {body: {status: 'processing'}}, {body: {status: 'completed', result}}]);
  const file = audioFile();
  assert.equal(await h.context.processMeeting(file), result);
  assert.equal(h.requests[0].url, 'http://127.0.0.1:8000/api/meetings/process?async=true');
  assert.equal(h.requests[0].options.method, 'POST');
  assert.deepEqual(h.requests[0].options.body.fields, [['audio', file, file.name]]);
  assert.equal(h.requests[1].url, 'http://127.0.0.1:8000/api/meetings/jobs/job%2F123');
  assert.equal(h.requests.length, 4);
  assert.equal(h.objectUrls[0], file);
});

test('missing, empty, and unsupported input fail before API requests', async () => {
  for (const file of [null, {...audioFile(), size: 0}, {...audioFile(), name: 'image.jpg'}]) {
    const h = harness();
    await assert.rejects(h.context.processMeeting(file));
    assert.equal(h.requests.length, 0);
    assert.equal(h.evaluate('state.data'), null);
  }
});

test('offline, failed job, malformed result and malformed status remain errors, never demo data', async () => {
  const cases = [
    [new Error('offline')],
    [{ok: false, status: 422, body: {detail: 'Invalid audio'}}],
    [{body: {}}],
    [{body: {job_id: 'x'}}, {body: {status: 'failed', error: 'Decoding failed'}}],
    [{body: {job_id: 'x'}}, {body: {status: 'completed', result: {}}}],
    [{body: {job_id: 'x'}}, {body: {status: 'surprise'}}],
    [{body: {job_id: 'x'}}, new Error('offline during polling')],
  ];
  for (const responses of cases) {
    const h = harness(responses);
    await assert.rejects(h.context.processMeeting(audioFile()));
    assert.equal(h.evaluate('state.data'), null);
  }
});

test('failed processing resets stale result and exposes an error, not a result page', async () => {
  const h = harness([new TypeError('Failed to fetch')]);
  h.context.fixture = resultFixture();
  h.evaluate('state.data = fixture');
  h.context.start(audioFile());
  await settle();
  assert.equal(h.evaluate('state.data'), null);
  assert.equal(h.element('errorBox').hidden, false);
  assert.match(h.element('errorMessage').textContent, /Нет соединения с сервером/);
  assert.equal(h.element('uploadView').hidden, false);
  assert.equal(h.element('resultView').hidden, true);
  assert.equal(h.element('exportTopBtn').hidden, true);
});

test('double start submits one upload and processing guard is released after success', async () => {
  const h = harness([{body: {job_id: 'x'}}, {body: {status: 'completed', result: resultFixture()}}]);
  const pending = h.context.start(audioFile());
  h.context.start(audioFile());
  assert.equal(h.evaluate('state.processing'), true);
  await pending;
  assert.equal(h.requests.filter(x => x.options.method === 'POST').length, 1);
  assert.equal(h.objectUrls.length, 1);
  assert.equal(h.evaluate('state.processing'), false);
  assert.equal(h.element('resultView').hidden, false);
});

test('processing failure releases guard and permits a real retry', async () => {
  const h = harness([new Error('offline'), {body: {job_id: 'retry'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioFile());
  assert.equal(h.evaluate('state.processing'), false);
  assert.equal(h.element('errorBox').hidden, false);
  await h.context.start(audioFile());
  assert.equal(h.requests.filter(x => x.options.method === 'POST').length, 2);
  assert.equal(h.evaluate('state.processing'), false);
  assert.equal(h.element('errorBox').hidden, true);
  assert.equal(h.element('resultView').hidden, false);
});

test('browse button suppresses label default activation and opens picker once', () => {
  const h = harness();
  h.events.get('DOMContentLoaded').forEach(handler => handler());
  let prevented = 0, stopped = 0;
  h.element('browseBtn').listeners.get('click').forEach(handler => handler({preventDefault() { prevented += 1; }, stopPropagation() { stopped += 1; }}));
  assert.equal(prevented, 1);
  assert.equal(stopped, 1);
  assert.equal(h.element('audioInput').clickCalls, 1);
});

test('timestamp playback seeks within the uploaded local audio object URL', async () => {
  const result = resultFixture();
  const h = harness([{body: {job_id: 'x'}}, {body: {status: 'completed', result}}]);
  h.context.fixture = await h.context.processMeeting(audioFile());
  h.evaluate('state.data = fixture; render(); seek(12, 0)');
  assert.equal(h.element('audioPlayer').src, 'blob:local-1');
  assert.equal(h.element('audioPlayer').currentTime, 12);
  assert.equal(h.element('audioPlayer').playCalls, 1);
  assert.match(h.element('nowPlaying').textContent, /00:12.*Prepare report/);
  assert.equal(h.requests.length, 2, 'playback must not download audio from backend');
});

test('completed real response persists JSON and audio; a fresh page restores it without API calls', async () => {
  const result = resultFixture();
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result}}]);
  await h.context.start(audioBlob());
  assert.equal(h.history.records.size, 1);
  const id = h.evaluate('state.historyId');
  const saved = await h.history.get(id);
  assert.deepEqual(saved.data, result);
  assert.equal(await saved.audio.text(), 'test-only audio bytes');
  assert.match(h.element('historyList').innerHTML, /meeting.mp3/);
  assert.equal(h.element('historyStatus').dataset.error, 'false');

  const reopened = harness([], h.history);
  await reopened.context.refreshHistory();
  assert.match(reopened.element('historyList').innerHTML, /meeting.mp3/);
  await reopened.context.openHistory(id);
  assert.equal(reopened.element('resultView').hidden, false);
  assert.equal(reopened.element('summaryText').textContent, result.summary.executive_summary);
  assert.equal(reopened.element('taskCount').textContent, 1);
  assert.equal(reopened.element('segmentCount').textContent, 2);
  reopened.context.seek(12, 0);
  assert.equal(reopened.element('audioPlayer').currentTime, 12);
  assert.equal(reopened.element('audioPlayer').playCalls, 1);
  assert.equal(reopened.requests.length, 0);
  assert.equal(reopened.objectUrls.length, 1);
});

test('assignee and deadline edits survive reopening; updates do not duplicate meetings or lose audio', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const id = h.evaluate('state.historyId');
  edit(h, 'assignee', 'Confirmed owner');
  edit(h, 'deadline', 'до конца недели');
  assert.equal(h.element('newMeetingBtn').disabled, true);
  h.context.newMeeting();
  assert.equal(h.evaluate('state.historyId'), id, 'pending writes prevent navigation races');
  await h.evaluate('historyWrites');
  assert.equal(h.history.records.size, 1);
  assert.equal(h.element('historyStatus').textContent, 'Изменения сохранены.');
  h.context.newMeeting();
  assert.equal(h.evaluate('state.data'), null);
  await h.context.openHistory(id);
  assert.equal(h.evaluate('state.data.action_items[0].assignee'), 'Confirmed owner');
  assert.equal(h.evaluate('state.data.action_items[0].deadline'), 'до конца недели');
  assert.equal(await (await h.history.get(id)).audio.text(), 'test-only audio bytes');
  assert.deepEqual(h.revokedUrls, ['blob:local-1']);
});

test('failed or malformed processing never creates a history record', async () => {
  for (const result of [{status: 'failed', error: 'Unable to decode'}, {status: 'completed', result: {}}]) {
    const h = harness([{body: {job_id: 'bad'}}, {body: result}]);
    await h.context.start(audioBlob());
    assert.equal(h.history.records.size, 0);
    assert.equal(h.evaluate('state.historyId'), null);
  }
});

test('storage failure keeps successful protocol visible, warns before leaving, and a later edit retries full save', async () => {
  const store = historyStore();
  const save = store.save;
  store.save = async () => { throw new Error('QuotaExceededError'); };
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}], store);
  await h.context.start(audioBlob());
  assert.equal(h.element('resultView').hidden, false);
  assert.equal(h.element('errorBox').hidden, true, 'storage failure is not a processing failure');
  assert.equal(h.element('historyStatus').dataset.error, 'true');
  assert.match(h.element('historyStatus').textContent, /Не удалось сохранить/);
  assert.equal(h.evaluate('state.historyUnsaved'), true);
  const data = h.evaluate('state.data');
  h.context.newMeeting();
  assert.equal(h.evaluate('state.data'), data, 'declining discard preserves unsaved result');
  await h.context.openHistory('missing');
  assert.equal(h.evaluate('state.data'), data);
  store.save = save;
  edit(h, 'assignee', 'Recovery owner');
  await h.evaluate('historyWrites');
  await settle();
  assert.equal(store.records.size, 1);
  assert.equal(h.evaluate('state.historyUnsaved'), false);
  assert.equal(h.element('historyStatus').dataset.error, 'false');
  assert.match(h.element('historyList').innerHTML, /meeting.mp3/);
  assert.equal([...store.records.values()][0].data.action_items[0].assignee, 'Recovery owner');
});

test('opening missing or damaged history preserves current valid result and audio', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const data = h.evaluate('state.data'), audioUrl = h.evaluate('state.audioUrl');
  for (const record of [null, {data: {}}, {data: resultFixture(), audio: null}]) {
    h.history.get = async () => record;
    await h.context.openHistory('bad');
    assert.equal(h.evaluate('state.data'), data);
    assert.equal(h.evaluate('state.audioUrl'), audioUrl);
    assert.equal(h.evaluate('state.restoring'), false);
    assert.equal(h.element('historyStatus').dataset.error, 'true');
  }
});

test('restoring prevents edit and upload races while stored JSON is being read', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const id = h.evaluate('state.historyId'), record = await h.history.get(id);
  let finishRead;
  h.history.get = () => new Promise(resolve => { finishRead = resolve; });
  const opening = h.context.openHistory(id);
  await settle();
  edit(h, 'assignee', 'Should not be accepted while loading');
  assert.equal(h.evaluate('state.data.action_items[0].assignee'), null);
  h.context.start(audioBlob());
  h.context.newMeeting();
  assert.equal(h.requests.length, 2);
  assert.equal(h.evaluate('state.historyId'), id);
  finishRead(record);
  await opening;
  assert.equal(h.evaluate('state.restoring'), false);
  assert.equal(h.element('newMeetingBtn').disabled, false);
});

test('sidebar escapes saved filenames and reports unavailable browser storage', async () => {
  const h = harness();
  h.history.list = async () => [{id: 'safe-id', fileName: '<img src=x onerror=alert(1)>', createdAt: '2026-09-23T08:00:00Z'}];
  await h.context.refreshHistory();
  assert.match(h.element('historyList').innerHTML, /&lt;img/);
  assert.doesNotMatch(h.element('historyList').innerHTML, /<img/);
  h.history.list = async () => { throw new Error('IndexedDB disabled'); };
  assert.equal(await h.context.refreshHistory(), false);
  assert.equal(h.element('historyStatus').dataset.error, 'true');
});

test('edits made during initial history save are committed after the original record, not overwritten', async () => {
  const store = historyStore(), save = store.save;
  let finishSave;
  store.save = record => new Promise(resolve => { finishSave = async () => { await save(record); resolve(); }; });
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}], store);
  const processing = h.context.start(audioBlob());
  await settle();
  assert.equal(h.element('resultView').hidden, false);
  edit(h, 'deadline', 'к среде');
  await finishSave();
  await processing;
  await h.evaluate('historyWrites');
  assert.equal([...store.records.values()][0].data.action_items[0].deadline, 'к среде');
  assert.equal(store.records.size, 1);
  assert.equal(h.evaluate('state.historyUnsaved'), false);
});

test('a failed edit save cannot silently restore stale stored data over unsaved changes', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const id = h.evaluate('state.historyId');
  h.history.updateData = async () => { throw new Error('Storage full'); };
  edit(h, 'assignee', 'Unsaved owner');
  await h.evaluate('historyWrites');
  await h.context.openHistory(id);
  assert.equal(h.evaluate('state.data.action_items[0].assignee'), 'Unsaved owner');
  assert.equal((await h.history.get(id)).data.action_items[0].assignee, null);
  assert.equal(h.element('historyStatus').dataset.error, 'true');
  let prevented = false;
  h.events.get('beforeunload')[0]({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
});

test('processing a second recording keeps the first and restores its original audio independently', async () => {
  const h = harness([
    {body: {job_id: 'one'}}, {body: {status: 'completed', result: resultFixture()}},
    {body: {job_id: 'two'}}, {body: {status: 'completed', result: {...resultFixture(), meeting: {title: 'Second meeting'}}}},
  ]);
  await h.context.start(audioBlob());
  const firstId = h.evaluate('state.historyId');
  h.context.newMeeting();
  const second = Object.assign(new Blob(['second audio'], {type: 'audio/mpeg'}), {name: 'second.mp3'});
  await h.context.start(second);
  assert.equal(h.history.records.size, 2);
  assert.notEqual(h.evaluate('state.historyId'), firstId);
  await h.context.openHistory(firstId);
  assert.equal(h.element('meetingTitle').textContent, 'meeting.mp3');
  assert.equal(await h.objectUrls.at(-1).text(), 'test-only audio bytes');
  assert.equal(h.requests.length, 4);
});

test('Save button persists task, assignee and deadline while preserving original evidence', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const id = h.evaluate('state.historyId');
  const inputs = Object.entries({task: 'Edited task title', assignee: 'Confirmed owner', deadline: 'к среде', source_quote: 'Must not replace evidence'})
    .map(([field, value]) => ({dataset: {field}, value}));
  const button = {dataset: {save: '0'}, disabled: false, textContent: 'Сохранить',
    closest: selector => selector === '.task-card' ? {querySelectorAll: () => inputs} : null};
  const event = {target: {closest: selector => selector === '[data-save]' ? button : null}};
  await Promise.all(h.events.get('click').map(handler => handler(event)));
  const saved = await h.history.get(id);
  assert.equal(saved.data.action_items[0].task, 'Edited task title');
  assert.equal(saved.data.action_items[0].assignee, 'Confirmed owner');
  assert.equal(saved.data.action_items[0].deadline, 'к среде');
  assert.equal(saved.data.action_items[0].source_quote, 'Prepare report');
  assert.equal(saved.data.action_items[0].timestamp_start, 12);
  assert.equal(saved.data.action_items[0].timestamp_end, 18);
  assert.equal(await saved.audio.text(), 'test-only audio bytes');
  assert.equal(button.textContent, 'Сохранено');
  assert.equal(button.disabled, false);
  assert.equal(h.history.records.size, 1);
  const reopened = harness([], h.history);
  await reopened.context.openHistory(id);
  assert.equal(reopened.evaluate('state.data.action_items[0].task'), 'Edited task title');
});

test('Save button reports storage failure instead of falsely claiming Saved', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const id = h.evaluate('state.historyId');
  h.history.updateData = async () => { throw new Error('Storage full'); };
  const inputs = Object.entries({task: 'Unsaved task title', assignee: 'Unsaved owner', deadline: 'до конца недели'})
    .map(([field, value]) => ({dataset: {field}, value}));
  const button = {dataset: {save: '0'}, disabled: false, textContent: 'Сохранить',
    closest: selector => selector === '.task-card' ? {querySelectorAll: () => inputs} : null};
  const event = {target: {closest: selector => selector === '[data-save]' ? button : null}};
  await Promise.all(h.events.get('click').map(handler => handler(event)));
  assert.equal(button.textContent, 'Не сохранено');
  assert.equal(button.disabled, false);
  assert.equal(h.evaluate('state.historyUnsaved'), true);
  assert.equal(h.element('historyStatus').dataset.error, 'true');
  assert.match(h.element('historyStatus').textContent, /Не удалось сохранить/);
  assert.equal(h.evaluate('state.data.action_items[0].task'), 'Unsaved task title');
  assert.equal(h.evaluate('state.data.action_items[0].source_quote'), 'Prepare report');
  assert.equal((await h.history.get(id)).data.action_items[0].task, 'Prepare report');
});

test('Russian result layout prioritizes actions and collapses the full transcript', () => {
  const sections = ['summaryHeading', 'tasksHeading', 'decisionsHeading', 'class="panel transcript-panel"'];
  const positions = sections.map(marker => htmlSource.indexOf(marker));
  assert.ok(positions.every((position, i) => position >= 0 && (!i || position > positions[i - 1])));
  assert.match(htmlSource, /<details class="panel transcript-panel">/);
  assert.match(htmlSource, /Загрузите запись совещания/);
  assert.match(htmlSource, /Система подготовит транскрипт, решения, поручения, ответственных и сроки/);
  assert.equal((htmlSource.match(/Скачать протокол DOCX/g) || []).length, 2);
  assert.doesNotMatch(htmlSource, /Meeting Protocol|Executive Summary|Action Items|Audio Evidence|Ready for review|Download DOCX/i);
});

test('task cards preserve evidence attributes and safely render multiline Russian edit controls', () => {
  const h = harness();
  const task = {...resultFixture().action_items[0], task: 'Проверить <условия> & сроки\nӘлия', timestamp_start: 134};
  const card = h.context.taskHtml(task, 2);
  assert.match(card, /<textarea[^>]*aria-label="Поручение 3"[^>]*data-field="task"[^>]*data-index="2"/);
  assert.match(card, /Проверить &lt;условия&gt; &amp; сроки\nӘлия/);
  assert.match(card, /data-evidence="134" data-index="2">▶ Проверить в аудио · 02:14/);
  assert.match(card, /Ответственный/);
  assert.match(card, /Срок исполнения/);
  assert.match(card, /data-save="2"/);
});

test('textarea and field edits preserve original evidence and reach the existing DOCX exporter', async () => {
  const h = harness();
  const result = resultFixture();
  result.action_items.push({...result.action_items[0], id: 'a2', task: 'Unchanged task'});
  const original = structuredClone(result);
  h.context.fixture = result;
  h.evaluate('state.data = fixture');
  const values = {task: 'Подготовить отчёт\nдля проверки', assignee: 'Әлия Қасымқызы', deadline: '30 сентября 2026'};
  const fields = Object.entries(values).map(([field, value]) => ({dataset: {field, index: '0'}, value,
    matches(selector) { assert.equal(selector, 'input[data-field],textarea[data-field]'); return true; }}));
  for (const target of fields) h.events.get('input').forEach(handler => handler({target}));
  const save = {dataset: {save: '0'}, closest() { return {querySelectorAll(selector) {
    assert.equal(selector, 'input[data-field],textarea[data-field]'); return fields;
  }}; }};
  await Promise.all(h.events.get('click').map(handler => handler({target: {closest: selector => selector === '[data-save]' ? save : null}})));
  assert.equal(save.textContent, 'Сохранено');
  assert.deepEqual(result.action_items[1], original.action_items[1]);
  for (const field of ['source_quote', 'timestamp_start', 'timestamp_end']) assert.equal(result.action_items[0][field], original.action_items[0][field]);
  for (const [field, value] of Object.entries(values)) assert.equal(result.action_items[0][field], value);
  h.context.exportDocx();
  const blob = h.objectUrls.at(-1);
  assert.equal(blob.type, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document');
  const bytes = new Uint8Array(await blob.arrayBuffer());
  assert.deepEqual([...bytes.slice(0, 4)], [80, 75, 3, 4]);
  const xml = new TextDecoder().decode(bytes);
  for (const value of Object.values(values)) assert.ok(xml.includes(value));
  assert.ok(xml.includes('ПРОТОКОЛ СОВЕЩАНИЯ'));
  assert.ok(xml.includes('Unchanged task'));
  assert.equal(h.downloads[0].download, 'meeting-protocol.docx');
  assert.equal(h.downloads[0].clickCalls, 1);
});

test('multiline task edit survives existing history persistence and reopening', async () => {
  const h = harness([{body: {job_id: 'stored'}}, {body: {status: 'completed', result: resultFixture()}}]);
  await h.context.start(audioBlob());
  const id = h.evaluate('state.historyId');
  edit(h, 'task', 'Подготовить отчёт\nӘлия Қасымқызы');
  await h.evaluate('historyWrites');
  h.context.newMeeting();
  await h.context.openHistory(id);
  assert.equal(h.evaluate('state.data.action_items[0].task'), 'Подготовить отчёт\nӘлия Қасымқызы');
  assert.equal(h.evaluate('state.data.action_items[0].source_quote'), 'Prepare report');
  assert.equal(h.evaluate('state.data.action_items[0].timestamp_start'), 12);
  assert.equal(h.requests.length, 2);
});
