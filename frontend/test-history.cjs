const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'history.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

// A small asynchronous IndexedDB contract double. It models atomic transactions,
// cursor iteration, request errors, delayed completion, and connection events.
function fakeIndexedDB() {
  const records = new Map();
  const environment = {records, opens: [], connections: [], transactions: [], holdCompletion: false, nextOpenError: null, blockNextOpen: false, nextPutError: null};
  let storeExists = false;
  environment.open = () => {
    const request = {};
    environment.opens.push(request);
    const database = {
      closed: false,
      objectStoreNames: {contains: name => storeExists && name === 'meetings'},
      createObjectStore(name, options) { assert.equal(name, 'meetings'); assert.equal(options.keyPath, 'id'); storeExists = true; },
      close() { this.closed = true; },
      transaction(name, mode) {
        if (this.closed) throw new Error('Connection is closed');
        assert.equal(name, 'meetings');
        const working = new Map(records);
        let pending = 0;
        let ended = false;
        const tx = {
          error: null,
          abort() {
            if (ended) return;
            ended = true;
            queueMicrotask(() => tx.onabort?.());
          },
          finish() {
            if (ended || pending) return;
            ended = true;
            if (mode === 'readwrite') {
              records.clear();
              for (const [key, value] of working) records.set(key, value);
            }
            tx.oncomplete?.();
          },
        };
        environment.transactions.push(tx);
        const checkComplete = () => queueMicrotask(() => { if (!environment.holdCompletion) tx.finish(); });
        function enqueue(run) {
          pending += 1;
          queueMicrotask(() => {
            if (!ended) run();
            pending -= 1;
            checkComplete();
          });
        }
        const store = {
          get(id) {
            const request = {};
            enqueue(() => { request.result = working.has(id) ? structuredClone(working.get(id)) : undefined; request.onsuccess?.(); });
            return request;
          },
          put(value) {
            const request = {};
            const copy = structuredClone(value);
            enqueue(() => {
              if (environment.nextPutError) {
                request.error = tx.error = environment.nextPutError;
                environment.nextPutError = null;
                tx.onerror?.({target: request});
                tx.abort();
              } else {
                working.set(copy.id, copy);
                request.result = copy.id;
                request.onsuccess?.();
              }
            });
            return request;
          },
          openCursor() {
            const request = {};
            const values = [...working.values()];
            let index = 0;
            const advance = () => enqueue(() => {
              request.result = index < values.length ? {value: structuredClone(values[index++]), continue: advance} : null;
              request.onsuccess?.();
            });
            advance();
            return request;
          },
        };
        tx.objectStore = () => store;
        checkComplete();
        return tx;
      },
    };
    environment.connections.push(database);
    request.result = database;
    request.transaction = {abort() { request.error = new Error('Upgrade aborted'); queueMicrotask(() => request.onerror?.()); }};
    request.succeed = () => {
      if (!storeExists) request.onupgradeneeded?.();
      request.onsuccess?.();
    };
    queueMicrotask(() => {
      if (environment.nextOpenError) {
        request.error = environment.nextOpenError;
        environment.nextOpenError = null;
        request.onerror?.();
      } else if (environment.blockNextOpen) {
        environment.blockNextOpen = false;
        request.onblocked?.();
      } else {
        request.succeed();
      }
    });
    return request;
  };
  return environment;
}

function load(indexedDB) {
  const context = vm.createContext({indexedDB});
  vm.runInContext(source, context, {filename: 'history.js'});
  return context.MeetingHistory;
}

function record(id = 'm1', createdAt = '2026-09-23T10:00:00.000Z') {
  return {id, fileName: `${id}.mp3`, createdAt, updatedAt: createdAt,
    data: {meeting: {title: id}, action_items: [{task: 'Original'}]}, audio: new Blob(['original audio'], {type: 'audio/mpeg'})};
}

test('save persists real result and audio, get survives a fresh module, list contains metadata only', async () => {
  const db = fakeIndexedDB();
  const history = load(db);
  await history.save(record());
  await history.save(record('m2', '2026-09-23T11:00:00.000Z'));
  const list = await history.list();
  assert.equal(list.map(x => x.id).join(','), 'm2,m1');
  for (const entry of list) assert.equal(Object.keys(entry).sort().join(','), 'createdAt,fileName,id,updatedAt');
  const restored = await load(db).get('m1');
  assert.equal(restored.data.action_items[0].task, 'Original');
  assert.equal(await restored.audio.text(), 'original audio');
  assert.equal(await history.get('missing'), null);
});

test('save resolves only after transaction completion, not successful put', async () => {
  const db = fakeIndexedDB();
  db.holdCompletion = true;
  const history = load(db);
  let resolved = false;
  const pending = history.save(record()).then(() => { resolved = true; });
  await tick();
  assert.equal(resolved, false);
  assert.equal(db.records.size, 0);
  db.transactions[0].finish();
  await pending;
  assert.equal(resolved, true);
  assert.equal(db.records.size, 1);
});

test('updateData preserves original audio and meeting metadata and waits for commit', async () => {
  const db = fakeIndexedDB();
  const history = load(db);
  const original = record();
  await history.save(original);
  db.holdCompletion = true;
  let resolved = false;
  const pending = history.updateData('m1', {action_items: [{task: 'Edited'}]}, '2026-09-23T12:00:00Z').then(() => { resolved = true; });
  await tick();
  assert.equal(resolved, false);
  assert.equal(db.records.get('m1').data.action_items[0].task, 'Original');
  db.transactions.at(-1).finish();
  await pending;
  db.holdCompletion = false;
  const updated = await history.get('m1');
  assert.equal(updated.data.action_items[0].task, 'Edited');
  assert.equal(updated.createdAt, original.createdAt);
  assert.equal(updated.fileName, original.fileName);
  assert.equal(updated.updatedAt, '2026-09-23T12:00:00Z');
  assert.equal(await updated.audio.text(), 'original audio');
});

test('updateData rejects a nonexistent id without creating a partial record', async () => {
  const db = fakeIndexedDB();
  await assert.rejects(load(db).updateData('missing', {}, Date.now()), /not present/);
  assert.equal(db.records.size, 0);
});

test('quota errors reject save and update without reporting success or losing audio', async () => {
  const db = fakeIndexedDB();
  const history = load(db);
  db.nextPutError = new DOMException('Quota exceeded', 'QuotaExceededError');
  await assert.rejects(history.save(record()), {name: 'QuotaExceededError'});
  assert.equal(db.records.size, 0);
  await history.save(record());
  db.nextPutError = new DOMException('Quota exceeded', 'QuotaExceededError');
  await assert.rejects(history.updateData('m1', {}, Date.now()), {name: 'QuotaExceededError'});
  const kept = await history.get('m1');
  assert.equal(kept.data.action_items[0].task, 'Original');
  assert.equal(await kept.audio.text(), 'original audio');
});

test('opening failure resets cached promise and a later operation can retry', async () => {
  const db = fakeIndexedDB();
  const history = load(db);
  db.nextOpenError = new Error('Storage denied');
  await assert.rejects(history.list(), /Storage denied/);
  assert.equal((await history.list()).length, 0);
  assert.equal(db.opens.length, 2);
});

test('blocked opening rejects promptly and a late connection is closed', async () => {
  const db = fakeIndexedDB();
  const history = load(db);
  db.blockNextOpen = true;
  await assert.rejects(history.list(), /blocked by another tab/);
  db.opens[0].succeed();
  assert.equal(db.connections[0].closed, true);
  assert.equal((await history.list()).length, 0);
});

test('version change closes cached connection and next operation opens a new one', async () => {
  const db = fakeIndexedDB();
  const history = load(db);
  await history.save(record());
  await history.list();
  assert.equal(db.opens.length, 1);
  db.connections[0].onversionchange();
  assert.equal(db.connections[0].closed, true);
  assert.equal((await history.get('m1')).id, 'm1');
  assert.equal(db.opens.length, 2);
});

test('missing IndexedDB and invalid record IDs are explicit errors', async () => {
  await assert.rejects(load(undefined).list(), /unavailable/);
  const history = load(fakeIndexedDB());
  await assert.rejects(history.get(''), /ID is required/);
  await assert.rejects(history.save({}), /ID is required/);
  await assert.rejects(history.updateData(null, {}, Date.now()), /ID is required/);
});
