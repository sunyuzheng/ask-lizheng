// Questions people asked, published as asked, chosen in the separate Ops service and
// served by lizheng.ai on this host. Anywhere else the reads fail quietly and the page keeps its examples.

// 最近问 and 最常问: two short lists, the same for every reader, so the CDN can serve them.
// How many questions each holds (Ops' DISCOVERY_LIST_SIZE).
export const DISCOVERY_LIST_SIZE = 30;
// Every published question has its own public page, which is what sharing one sends.
export const discoveryPage = id => `https://www.lizheng.ai/ask/${id}`;
const ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const text = value => typeof value === 'string' && value.trim().length > 0;
const count = value => Number.isInteger(value) && value >= 0;
const card = value => !!value && typeof value === 'object' && ID.test(value.public_id) && count(value.revision)
  && (value.topic_key === undefined || typeof value.topic_key === 'string') && text(value.question) && typeof value.summary === 'string' && typeof value.topic_label === 'string'
  && count(value.topic_question_count) && count(value.likes);
// The answer renders with the conversation's own components, so only the fields they read, as strings.
const SOURCE_FIELDS = ['id', 'title', 'url', 'date', 'source_type', 'excerpt', 'reason', 'timecode', 'author', 'attribution_note', 'public_copy_url',
  'source_visibility', 'text_access', 'membership_platform', 'membership_url', 'membership_verified_at', 'transcript_source_kind', 'transcript_quality', 'speaker_classification'];
const source = value => (value && /^S\d+$/.test(value.id) && text(value.title) && /^https:\/\//.test(value.url)
  ? Object.fromEntries(SOURCE_FIELDS.filter(key => typeof value[key] === 'string'
    && (!['public_copy_url', 'membership_url'].includes(key) || /^https:\/\//.test(value[key]))).map(key => [key, value[key]]))
  : null);
const section = value => (value && typeof value.heading === 'string' && typeof value.body === 'string'
  ? {heading: value.heading, body: value.body, kind: typeof value.kind === 'string' ? value.kind : undefined,
    source_ids: Array.isArray(value.source_ids) ? value.source_ids.filter(id => typeof id === 'string' && /^S\d+$/.test(id)) : []}
  : null);

/** Both lists in one read; null when they cannot be read (other hosts, Ops down). */
export async function discoveryLists(signal) {
  try {
    const response = await fetch('/api/ask-lizheng/discovery/lists', {credentials: 'omit', signal});
    if (!response.ok) return null;
    const value = await response.json();
    if (!Array.isArray(value?.recent) || !Array.isArray(value?.frequent)) return null;
    return {recent: value.recent.filter(card), frequent: value.frequent.filter(card)};
  } catch { return null; }
}

// When a question was asked, the way people say it: 刚刚, 23分钟前, 3小时前, 2天前, then the date.
// Ops gives the time to five minutes; a question shows up 15 to 30 minutes after it is asked.
export function askedAgo(iso, now = Date.now()) {
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return '';
  const minutes = Math.max(0, Math.floor((now - time) / 60000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes}分钟前`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)}小时前`;
  if (minutes < 7 * 24 * 60) return `${Math.floor(minutes / (24 * 60))}天前`;
  const date = new Date(time);
  return `${date.getFullYear() === new Date(now).getFullYear() ? '' : `${date.getFullYear()}年`}${date.getMonth() + 1}月${date.getDate()}日`;
}

// Two questions that differ only in punctuation or a word or two read as one, even when the
// curation filed them under different topics; only one of them is shown.
const shape = text => String(text || '').replace(/[\s\p{P}\p{S}]/gu, '').toLowerCase();
const PAIRS = new Map();
function pairs(text) {
  if (!PAIRS.has(text)) {
    const s = shape(text), out = new Set();
    for (let i = 0; i < s.length - 1; i++) out.add(s.slice(i, i + 2));
    PAIRS.set(text, out);
  }
  return PAIRS.get(text);
}
export function sameQuestion(a, b) {
  const x = pairs(a), y = pairs(b);
  if (!x.size || !y.size) return shape(a) === shape(b);
  let both = 0;
  for (const pair of x) if (y.has(pair)) both++;
  // Mostly the same characters, or the shorter one nearly contained in the longer (a question
  // with 「AI时代」 added). On the 46 questions published by 2026-10-03, this matched only three
  // rewordings of one question.
  return both / (x.size + y.size - both) >= 0.6 || (Math.min(x.size, y.size) >= 6 && both / Math.min(x.size, y.size) >= 0.8);
}

const askedTime = card => Date.parse(card.asked_at) || 0;

// How many similar askings a card stands for: its topic's count, plus the same question asked
// in other words that the curation filed under another topic.
export const similarCount = (card, cards) => card.topic_question_count + new Set(cards
  .filter(other => other.public_id !== card.public_id && other.topic_key !== card.topic_key && sameQuestion(other.question, card.question))
  .map(other => other.public_id)).size;

// One question asked in other words shows once: as the wording asked most recently (a common
// question written fresh, never asked, counts as oldest), with the similar askings of them all.
export function foldDiscovery(cards) {
  const groups = [];
  for (const card of cards) {
    if (groups.some(group => group.some(other => other.public_id === card.public_id))) continue;
    const group = groups.find(group => group.some(other => sameQuestion(other.question, card.question)));
    if (group) group.push(card); else groups.push([card]);
  }
  return groups.map(group => {
    const card = group.reduce((a, b) => askedTime(b) > askedTime(a) ? b : a);
    return {...card, similar_count: similarCount(card, group)};
  });
}

// How many published questions were asked in the last day. 最近问 holds the 30 newest, so
// below 30 the count is exact; at 30 there may be more, and the page says 30+.
export const askedLastDay = (pool, now = Date.now()) => pool.filter(card => now - Date.parse(card.asked_at) < 24 * 3600000).length;

const topicOf = card => card.topic_key || card.public_id;
const often = (a, b) => b.similar_count - a.similar_count || b.likes - a.likes
  || Number(!!askedTime(a)) - Number(!!askedTime(b)) || Date.parse(b.published_at) - Date.parse(a.published_at);

// What a list shows, the same for everyone; one question asked two ways shows once, as its wording
// asked most recently, counting the similar askings of them all (over both lists).
// 最近问 (recent): the questions people asked, most recently asked first; a common question written
// fresh was never asked and is left to 最常问.
// 最常问 (frequent): each topic once, through its question asked most recently, ranked by similar
// askings, then likes, then a common question written fresh before a single asking, then newest.
// The same rules as lizheng.ai's homepage, which tests them.
export function discoveryView(lists, view) {
  const pool = new Map();
  for (const card of [...lists.frequent, ...lists.recent]) if (!pool.has(card.public_id)) pool.set(card.public_id, card);
  const cards = foldDiscovery([...pool.values()]);
  if (view === 'recent') return cards.filter(askedTime).sort((a, b) => askedTime(b) - askedTime(a)).map(card => ({...card, role: 'fresh'}));
  const latest = new Map();
  for (const card of cards) if (!latest.has(topicOf(card)) || askedTime(card) > askedTime(latest.get(topicOf(card)))) latest.set(topicOf(card), card);
  return [...latest.values()].sort(often).map(card => ({...card, role: 'common'}));
}

export async function discoveryDetail(id, signal) {
  try {
    const response = await fetch(`/api/ask-lizheng/discovery/detail?public_id=${encodeURIComponent(id)}`, {cache: 'no-store', credentials: 'omit', signal});
    if (!response.ok) return null;
    const value = await response.json();
    const answer = value?.answer;
    if (!text(value?.question) || !answer || typeof answer.summary !== 'string' || !Array.isArray(answer.sections) || !Array.isArray(answer.sources)) return null;
    return {question: value.question, answer: {
      summary: answer.summary,
      sections: answer.sections.map(section).filter(Boolean),
      sources: answer.sources.map(source).filter(Boolean),
      limitations: text(answer.limitations) ? answer.limitations : '',
    }};
  } catch { return null; }
}

/** Signed-in accounts only; the site derives the voter from its own session. */
export async function voteDiscovery(id, revision, vote) {
  try {
    const response = await fetch('/api/ask-lizheng/discovery/vote', {
      method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({public_id: id, expected_revision: revision, vote}),
    });
    if (!response.ok) return null;
    const value = await response.json();
    return count(value?.likes) && typeof value?.voted === 'boolean' ? {likes: value.likes, voted: value.voted} : null;
  } catch { return null; }
}
