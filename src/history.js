// 我的提问 (2026-10-05, the user's decision): the questions asked on this device, each with its answer,
// kept in this browser only. Questions are kept with no name, account, email or IP, so we cannot tell
// which were yours and have no history to give back; this list is the only one, and the page says so.
// Never the situation: it is not kept anywhere (an answer may still mention it). Clearing the
// browser's data, another browser or another device starts empty.
const KEY = 'ask-history-v1';
// About 50 answers fit well inside what a browser keeps for one site (a few megabytes).
export const HISTORY_SIZE = 50;

const text = value => typeof value === 'string' && value.trim().length > 0;
const strings = value => Array.isArray(value) ? value.filter(item => typeof item === 'string') : [];
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
    // Lets the person who asked share it later, as from the conversation (share-link.js).
    ...(share && typeof share.record_id === 'string' && typeof share.word === 'string' && typeof share.proof === 'string'
      ? {share: {record_id: share.record_id, word: share.word, proof: share.proof}} : {}),
  };
}
function entry(value) {
  if (!value || typeof value !== 'object' || !text(value.id) || !text(value.question) || !Number.isFinite(Date.parse(value.asked_at))) return null;
  const answer = result(value.result);
  return answer && {id: value.id, asked_at: value.asked_at, question: value.question, intent: typeof value.intent === 'string' ? value.intent : 'understand',
    personal: value.personal === true, model: typeof value.model === 'string' ? value.model : '', result: answer};
}

/** This device's questions, newest first; empty when the browser keeps nothing. */
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
/** Keeps an answered question (asked again, it replaces itself); returns the list as kept. */
export function keepAsked(value) {
  const item = entry(value);
  if (!item) return readHistory();
  return write([item, ...readHistory().filter(other => other.id !== item.id)].slice(0, HISTORY_SIZE));
}
export const forgetAsked = id => write(readHistory().filter(item => item.id !== id));
export const forgetAll = () => write([]);
