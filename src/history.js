// 我的提问 (2026-10-05, the user's decision): the questions asked on this device, each with its answer,
// kept in this browser only. Questions are kept with no name, account, email or IP, so we cannot tell
// which were yours and have no history to give back; this list is the only one, and the page says so.
// Never the situation: it is not kept anywhere (an answer may still mention it). Clearing the
// browser's data, another browser or another device starts empty.
//
// One entry is one conversation (2026-10-15, the owner: 多轮对话…不要起一个新的标题), titled by its
// first question, with every answered question in it, the conversation it continues on the server
// (conversation_id, so a follow-up asked after reopening it goes on the same page) and that page.
// Entries kept before then were one question each: each reads as a conversation of one.
import {isAnswerPage} from './share-link.js';

const KEY = 'ask-history-v1';
// About 50 conversations fit well inside what a browser keeps for one site (a few megabytes).
export const HISTORY_SIZE = 50;
// The questions of one conversation kept, its first ones and its latest ones.
export const TURNS_KEPT = 20;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const text = value => typeof value === 'string' && value.trim().length > 0;
const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];
const when = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
// What reopening an answer needs, as the conversation renders it; nothing else of the result.
function result(value) {
  if (!value || typeof value !== 'object' || value.status !== 'answered' || typeof value.summary !== 'string'
    || !Array.isArray(value.sections) || !Array.isArray(value.sources)) return null;
  const share = value.share;
  return {
    status: 'answered',
    summary: value.summary,
    sections: value.sections.filter(section => section && typeof section.heading === 'string' && typeof section.body === 'string'),
    sources: value.sources.filter(source => source && typeof source.id === 'string' && typeof source.url === 'string'),
    limitations: typeof value.limitations === 'string' ? value.limitations : '',
    followups: strings(value.followups),
    // Lets the person who asked share it later, as from the conversation (share-link.js), and its page.
    ...(share && typeof share.record_id === 'string' && typeof share.word === 'string' && typeof share.proof === 'string'
      ? {share: {record_id: share.record_id, word: share.word, proof: share.proof, ...(isAnswerPage(share.url) ? {url: share.url} : {})}} : {}),
  };
}
function turn(value) {
  if (!value || typeof value !== 'object' || !text(value.question) || !when(value.asked_at)) return null;
  const answer = result(value.result);
  return answer && {id: text(value.id) ? value.id : value.asked_at, asked_at: value.asked_at, question: value.question,
    intent: typeof value.intent === 'string' ? value.intent : 'understand', personal: value.personal === true,
    model: typeof value.model === 'string' ? value.model : '', result: answer,
    // Asked by someone else (kept so only on 2026-10-15, before a conversation asked on someone
    // else's page counted as its asker's own from its first question).
    ...(value.shared === true ? {shared: true} : {})};
}
function entry(value) {
  if (!value || typeof value !== 'object' || !text(value.id)) return null;
  // Kept before 2026-10-15: one question, the entry itself.
  const turns = (Array.isArray(value.turns) ? value.turns : [value]).map(turn).filter(Boolean);
  if (!turns.length) return null;
  const kept = turns.length > TURNS_KEPT ? [...turns.slice(0, 1), ...turns.slice(1 - TURNS_KEPT)] : turns;
  return {id: value.id, asked_at: kept[0].asked_at, question: kept[0].question, turns: kept,
    ...(UUID.test(value.conversation_id) ? {conversation_id: value.conversation_id} : {}),
    ...(isAnswerPage(value.url) ? {url: value.url} : {})};
}

/** This device's conversations, the one asked in last first; empty when the browser keeps nothing. */
export function readHistory() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(value) ? value.map(entry).filter(Boolean) : [];
  } catch { return []; }
}
function write(list) {
  // When the browser's room runs out, the oldest go first.
  for (let keep = list.length; keep > 0; keep = Math.floor(keep / 2)) {
    try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, keep))); return list.slice(0, keep); } catch { /* fewer */ }
  }
  try { localStorage.removeItem(KEY); } catch { /* nothing kept */ }
  return [];
}
/** Keeps a conversation as it now is, first in the list (kept again, it replaces itself); returns
 * the list as kept. `turns` are its answered questions in order. */
export function keepConversation(value) {
  const item = entry(value);
  if (!item) return readHistory();
  return write([item, ...readHistory().filter(other => other.id !== item.id)].slice(0, HISTORY_SIZE));
}
/** The conversation kept on this device whose page this is, if any. */
export const conversationAt = url => (isAnswerPage(url) ? readHistory().find(item => item.url === url) || null : null);
export const forgetAsked = id => write(readHistory().filter(item => item.id !== id));
export const forgetAll = () => write([]);
