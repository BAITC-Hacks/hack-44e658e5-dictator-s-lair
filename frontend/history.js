/* Browser-local meeting storage. Audio never leaves this module for persistence. */
(function (root) {
  'use strict';

  const DATABASE = 'hackalem-meeting-history';
  const STORE = 'meetings';
  let databasePromise = null;

  function openDatabase() {
    if (databasePromise) return databasePromise;
    const pending = new Promise((resolve, reject) => {
      if (!root.indexedDB) {
        reject(new Error('Local meeting history is unavailable in this browser.'));
        return;
      }
      let settled = false;
      const fail = error => {
        if (settled) return;
        settled = true;
        reject(error || new Error('Could not open local meeting history.'));
      };
      const request = root.indexedDB.open(DATABASE, 1);
      request.onupgradeneeded = () => {
        if (settled) {
          request.transaction.abort();
          return;
        }
        try {
          if (!request.result.objectStoreNames.contains(STORE)) {
            request.result.createObjectStore(STORE, {keyPath: 'id'});
          }
        } catch (error) {
          request.transaction.abort();
          fail(error);
        }
      };
      request.onerror = () => fail(request.error);
      request.onblocked = () => fail(new Error('Local meeting history is blocked by another tab. Close other tabs and retry.'));
      request.onsuccess = () => {
        const database = request.result;
        if (settled) {
          database.close();
          return;
        }
        settled = true;
        const invalidate = () => {
          if (databasePromise === pending) databasePromise = null;
        };
        database.onversionchange = () => { database.close(); invalidate(); };
        database.onclose = invalidate;
        resolve(database);
      };
    });
    databasePromise = pending;
    pending.catch(() => { if (databasePromise === pending) databasePromise = null; });
    return pending;
  }

  async function transaction(mode, operation) {
    const database = await openDatabase();
    return new Promise((resolve, reject) => {
      let tx;
      let result;
      try {
        tx = database.transaction(STORE, mode);
      } catch (error) {
        // A closed/invalidated connection must not poison future retry attempts.
        database.close();
        databasePromise = null;
        reject(error);
        return;
      }
      tx.oncomplete = () => resolve(result);
      tx.onabort = () => reject(tx.error || new Error('Saving local meeting history was aborted.'));
      tx.onerror = event => reject(tx.error || event.target.error || new Error('Local meeting history could not be stored.'));
      const fail = error => {
        try { tx.abort(); } catch (_) { /* The transaction may already be inactive. */ }
        reject(error);
      };
      try {
        operation(tx.objectStore(STORE), value => { result = value; }, fail);
      } catch (error) {
        fail(error);
      }
    });
  }

  function requireId(id) {
    if (typeof id !== 'string' || !id.trim()) throw new Error('A meeting history ID is required.');
  }

  function dateValue(value) {
    const number = typeof value === 'number' ? value : Date.parse(value);
    return Number.isFinite(number) ? number : 0;
  }

  async function list() {
    return transaction('readonly', (store, done) => {
      const records = [];
      const request = store.openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor) {
          records.sort((a, b) => dateValue(b.createdAt) - dateValue(a.createdAt) || dateValue(b.updatedAt) - dateValue(a.updatedAt));
          done(records);
          return;
        }
        const value = cursor.value;
        // Keep only metadata in the list; do not accumulate audio blobs or transcripts.
        records.push({id: value.id, fileName: value.fileName, createdAt: value.createdAt, updatedAt: value.updatedAt});
        cursor.continue();
      };
    });
  }

  async function get(id) {
    requireId(id);
    return transaction('readonly', (store, done) => {
      const request = store.get(id);
      request.onsuccess = () => done(request.result || null);
    });
  }

  async function save(record) {
    requireId(record && record.id);
    return transaction('readwrite', (store, done) => {
      store.put(record);
      done(record);
    });
  }

  async function updateData(id, data, updatedAt) {
    requireId(id);
    return transaction('readwrite', (store, done, fail) => {
      const request = store.get(id);
      request.onsuccess = () => {
        try {
          if (!request.result) throw new Error('The meeting is not present in local history.');
          const record = {...request.result, data, updatedAt};
          store.put(record);
          done(record);
        } catch (error) {
          fail(error);
        }
      };
    });
  }

  root.MeetingHistory = {list, get, save, updateData};
})(globalThis);
