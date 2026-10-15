// 我的提问 (src/history.js): one entry per conversation since 2026-10-15. Synthetic questions only.
import assert from 'node:assert/strict';
import {beforeEach, test} from 'node:test';

const store = new Map();
globalThis.localStorage = {
  getItem: key => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => { store.set(key, String(value)); },
  removeItem: key => { store.delete(key); },
};
const {conversationAt, forgetAsked, keepConversation, readHistory, TURNS_KEPT} = await import('../src/history.js');

const answer = (extra = {}) => ({status: 'answered', summary: '合成摘要', sections: [{heading: '合成', body: '合成正文'}],
  sources: [{id: 'S1', url: 'https://example.test/a'}], limitations: '', followups: ['合成追问？'], ...extra});
const turn = (n, extra = {}) => ({id: String(n), asked_at: new Date(Date.UTC(2026, 9, 15, 0, n)).toISOString(), question: `合成问题${n}？`,
  intent: 'understand', personal: false, model: 'AI', result: answer(), ...extra});
const PAGE = 'https://ask.lizheng.ai/s/2026-10-15/career-choice';
const UUID = '00000000-0000-4000-8000-000000000001';

beforeEach(() => store.clear());

test('a conversation is one entry, titled by its first question, kept again as it grows', () => {
  keepConversation({id: UUID, conversation_id: UUID, url: PAGE, turns: [turn(1)]});
  keepConversation({id: 'other', turns: [turn(5)]});
  const list = keepConversation({id: UUID, conversation_id: UUID, url: PAGE, turns: [turn(1), turn(2), turn(3, {shared: true})]});
  assert.deepEqual(list.map(item => item.id), [UUID, 'other']);
  const [first] = readHistory();
  assert.equal(first.question, '合成问题1？');
  assert.equal(first.asked_at, turn(1).asked_at);
  assert.deepEqual(first.turns.map(t => t.question), ['合成问题1？', '合成问题2？', '合成问题3？']);
  assert.equal(first.turns[2].shared, true);
  assert.equal(first.conversation_id, UUID);
  assert.equal(conversationAt(PAGE)?.id, UUID);
  assert.equal(conversationAt('https://example.test/elsewhere'), null);
});

test('an entry kept before conversations were reads as a conversation of one', () => {
  store.set('ask-history-v1', JSON.stringify([{...turn(1), result: answer({share: {record_id: 'r', word: 'w', proof: 'p', url: PAGE}})}]));
  const [item] = readHistory();
  assert.equal(item.id, '1');
  assert.equal(item.turns.length, 1);
  assert.equal(item.conversation_id, undefined);
  // The page an answer was given with is kept with it.
  assert.equal(item.turns[0].result.share.url, PAGE);
  assert.deepEqual(forgetAsked('1'), []);
});

test('keeps only what reopening needs, and a long conversation by its first and latest questions', () => {
  keepConversation({id: UUID, conversation_id: 'not-a-uuid', url: 'https://evil.test/s/x', turns: [
    turn(1, {result: answer({extra: 'dropped', share: {record_id: 'r', word: 'w', proof: 'p', url: 'https://evil.test/'}})}),
    {question: '', asked_at: 'x', result: answer()}, turn(2, {result: {status: 'clarify'}}),
  ]});
  const [item] = readHistory();
  assert.equal(item.turns.length, 1);
  assert.equal(item.conversation_id, undefined);
  assert.equal(item.url, undefined);
  assert.equal(item.turns[0].result.extra, undefined);
  assert.deepEqual(item.turns[0].result.share, {record_id: 'r', word: 'w', proof: 'p'});
  const many = Array.from({length: TURNS_KEPT + 5}, (_, i) => turn(i + 1));
  keepConversation({id: 'long', turns: many});
  const long = readHistory().find(entry => entry.id === 'long');
  assert.equal(long.turns.length, TURNS_KEPT);
  assert.equal(long.turns[0].question, '合成问题1？');
  assert.equal(long.turns.at(-1).question, `合成问题${TURNS_KEPT + 5}？`);
});
