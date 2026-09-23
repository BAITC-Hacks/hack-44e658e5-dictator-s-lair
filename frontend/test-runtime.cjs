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

function harness(responses = []) {
  const elements = new Map();
  const events = new Map();
  const requests = [];
  const objectUrls = [];
  const revokedUrls = [];
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
      });
    }
    return elements.get(id);
  }
  const context = vm.createContext({
    AbortController,
    TypeError,
    console: {log() {}, error() {}},
    document: {
      getElementById: element,
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
  return {context, element, events, requests, objectUrls, revokedUrls, evaluate: code => vm.runInContext(code, context)};
}

const audioFile = () => ({name: 'meeting.mp3', type: 'audio/mpeg', size: 1234});
const settle = () => new Promise(resolve => setImmediate(resolve));

test('the shipped page loads app.js only and has no runtime mock/demo path', () => {
  assert.deepEqual([...htmlSource.matchAll(/<script\s+src="([^"]+)"/g)].map(x => x[1]), ['./app.js']);
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
    assert.throws(() => h.context.normalize(malformed), /response|malformed/i);
  }
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
    assert.throws(() => h.context.normalize(result), /malformed meeting JSON.*Повторите обработку/);
  }
  const valid = resultFixture();
  valid.summary.decisions = ['Approved', {text: 'Deferred', source_quote: 'Next week'}];
  assert.equal(h.context.normalize(valid), valid);
});

test('render prefers speaker names, falls back to IDs, and never guesses task owner from talker', () => {
  const h = harness();
  h.context.fixture = resultFixture();
  h.evaluate('state.data = fixture; render()');
  assert.match(h.element('transcriptList').innerHTML, /Named chair/);
  assert.match(h.element('transcriptList').innerHTML, /SPEAKER_02/);
  assert.match(h.element('taskList').innerHTML, /data-field="assignee"[^>]*value=""[^>]*placeholder="Не определён"/);
  assert.match(h.element('decisionList').innerHTML, /Approved/);
  const task = {...resultFixture().action_items[0], assignee_speaker_id: 'SPEAKER_02'};
  assert.match(h.context.taskHtml(task, 0), /data-field="assignee"[^>]*value="SPEAKER_02"/);
  task.assignee = 'Named owner';
  assert.match(h.context.taskHtml(task, 0), /data-field="assignee"[^>]*value="Named owner"/);
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
  assert.match(h.element('errorMessage').textContent, /Нет соединения с backend/);
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
