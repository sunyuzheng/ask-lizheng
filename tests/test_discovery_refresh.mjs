import test from 'node:test';
import assert from 'node:assert/strict';
import {changedDiscoveryIds, watchDiscoveryLists, DISCOVERY_REFRESH_MS} from '../src/discovery-refresh.js';

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
function setup(t, read) {
  t.mock.timers.enable({apis: ['setTimeout', 'setInterval', 'Date'], now: 10000});
  const win = new EventTarget(), doc = new EventTarget();
  doc.visibilityState = 'visible';
  const received = [], failures = [];
  const watcher = watchDiscoveryLists({read, window: win, document: doc,
    onLists: lists => received.push(lists), onUnavailable: () => failures.push(true)});
  t.after(() => watcher.stop());
  return {win, doc, received, failures, watcher};
}

test('loads on entry and each visible minute; background pages pause and resume once', async t => {
  let reads = 0;
  const s = setup(t, async () => ++reads);
  await flush();
  assert.deepEqual(s.received, [1]);
  t.mock.timers.tick(DISCOVERY_REFRESH_MS);
  await flush();
  assert.deepEqual(s.received, [1, 2]);
  s.doc.visibilityState = 'hidden';
  s.doc.dispatchEvent(new Event('visibilitychange'));
  t.mock.timers.tick(DISCOVERY_REFRESH_MS * 3);
  await flush();
  assert.equal(reads, 2);
  s.doc.visibilityState = 'visible';
  s.doc.dispatchEvent(new Event('visibilitychange'));
  s.win.dispatchEvent(new Event('focus'));
  await flush();
  assert.deepEqual(s.received, [1, 2, 3]);
});

test('a timed-out read cannot overwrite a retry, even if it ignores cancellation', async t => {
  let finish, signal, reads = 0;
  const s = setup(t, incoming => {
    if (++reads === 1) { signal = incoming; return new Promise(resolve => { finish = resolve; }); }
    return Promise.resolve('new');
  });
  await flush();
  s.watcher.refresh();
  assert.equal(reads, 1);
  t.mock.timers.tick(8000);
  assert.equal(signal.aborted, true);
  assert.equal(s.failures.length, 1);
  s.watcher.refresh();
  await flush();
  finish('old');
  await flush();
  assert.deepEqual(s.received, ['new']);
});

test('leaving the home page aborts a read and stops timers and focus callbacks', async t => {
  let finish, signal, reads = 0;
  const s = setup(t, incoming => { reads++; signal = incoming; return new Promise(resolve => { finish = resolve; }); });
  await flush();
  s.watcher.stop();
  assert.equal(signal.aborted, true);
  finish('late');
  t.mock.timers.tick(DISCOVERY_REFRESH_MS * 2);
  s.win.dispatchEvent(new Event('focus'));
  await flush();
  assert.equal(reads, 1);
  assert.deepEqual(s.received, []);
});

test('a failed read recovers on a later poll', async t => {
  let reads = 0;
  const s = setup(t, async () => { if (++reads === 1) throw new Error('offline'); return 'back'; });
  await flush();
  assert.equal(s.failures.length, 1);
  t.mock.timers.tick(DISCOVERY_REFRESH_MS);
  await flush();
  assert.deepEqual(s.received, ['back']);
});

test('a quick background round trip restarts a canceled initial read without waiting a minute', async t => {
  let reads = 0;
  const s = setup(t, () => ++reads === 1 ? new Promise(() => {}) : Promise.resolve('resumed'));
  await flush();
  s.doc.visibilityState = 'hidden';
  s.doc.dispatchEvent(new Event('visibilitychange'));
  t.mock.timers.tick(100);
  s.doc.visibilityState = 'visible';
  s.doc.dispatchEvent(new Event('visibilitychange'));
  await flush();
  assert.deepEqual(s.received, ['resumed']);
});

test('withdrawals and content revisions apply immediately; additions and likes can wait', () => {
  const shown = [{public_id: 'a', revision: 1, question: 'first', summary: 'answer'}, {public_id: 'b', revision: 1, question: 'second'}];
  const lists = (recent, frequent = []) => ({recent, frequent});
  assert.deepEqual([...changedDiscoveryIds(shown, lists([...shown, {public_id: 'c'}]))], []);
  assert.deepEqual([...changedDiscoveryIds(shown, lists(shown.map(card => ({...card, likes: 3}))))], []);
  assert.deepEqual([...changedDiscoveryIds(shown, lists([shown[1]]))], ['a']);
  assert.deepEqual([...changedDiscoveryIds(shown, lists([{...shown[0], revision: 2}, shown[1]]))], ['a']);
  assert.deepEqual([...changedDiscoveryIds(shown, lists([{...shown[0], summary: 'edited'}, shown[1]]))], ['a']);
  assert.deepEqual([...changedDiscoveryIds([shown[1]], lists([shown[0]], [{...shown[0], similar: [shown[1]]}]))], []);
});
