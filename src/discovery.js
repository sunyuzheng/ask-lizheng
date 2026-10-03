// Published, de-identified questions people asked, curated in the separate Ops service and
// served by lizheng.ai on this host. Anywhere else the reads fail quietly and the page keeps its examples.

// The pool a visit draws from: every topic once, most asked first, up to three pages of 20 (the
// common questions written fresh, asked once, rank last), and the 20 newest questions.
const VIEWS = [['window=all&sort=frequent&limit=20', 3], ['window=all&sort=recent&limit=20', 1]];
const CURSOR = /^[A-Za-z0-9_-]{1,512}$/;
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

async function list(view, pages, signal) {
  const items = [];
  let cursor = null;
  try {
    for (let page = 0; page < pages; page++) {
      const response = await fetch(`/api/ask-lizheng/discovery/questions?${view}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
        {cache: 'no-store', credentials: 'omit', signal});
      if (!response.ok) break;
      const value = await response.json();
      if (Array.isArray(value?.items)) items.push(...value.items.filter(card));
      cursor = typeof value?.next_cursor === 'string' && CURSOR.test(value.next_cursor) ? value.next_cursor : null;
      if (!cursor) break;
    }
  } catch {}
  return items;
}

/** Every question a page at a time: newest first (recent), or each topic once, most asked first
 * (frequent). An expired cursor means: start again. */
export async function discoveryPage(cursor, sort = 'recent') {
  try {
    const response = await fetch(`/api/ask-lizheng/discovery/questions?window=all&sort=${sort === 'frequent' ? 'frequent' : 'recent'}&limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      {cache: 'no-store', credentials: 'omit'});
    if (response.status === 409) return {expired: true, items: [], next: null};
    if (!response.ok) return null;
    const value = await response.json();
    const next = typeof value?.next_cursor === 'string' && CURSOR.test(value.next_cursor) ? value.next_cursor : null;
    return {expired: false, items: Array.isArray(value?.items) ? value.items.filter(card) : [], next};
  } catch { return null; }
}

export async function discoveryPool(signal) {
  const pool = new Map();
  for (const item of (await Promise.all(VIEWS.map(([view, pages]) => list(view, pages, signal)))).flat()) {
    if (!pool.has(item.public_id)) pool.set(item.public_id, item);
  }
  return [...pool.values()];
}

// What this browser was shown before, oldest first. Without storage every visit is a first visit.
const SEEN = 'ask-discovery-seen';
export function readSeen() {
  try {
    const value = JSON.parse(localStorage.getItem(SEEN) || '[]');
    return Array.isArray(value) ? value.filter(id => typeof id === 'string' && ID.test(id)) : [];
  } catch { return []; }
}
export function rememberSeen(seen, shown) {
  try { localStorage.setItem(SEEN, JSON.stringify([...seen.filter(id => !shown.includes(id)), ...shown].slice(-60))); } catch {}
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

// How many published questions were asked in the last day. The pool holds the 20 newest, so
// below 20 the count is exact; at 20 there may be more, and the page says 20+.
export const askedLastDay = (pool, now = Date.now()) => pool.filter(card => now - Date.parse(card.asked_at) < 24 * 3600000).length;

// The questions a visit shows, taking turns: the most recently asked (role fresh), then the most
// often asked (role common). Each topic stands for itself once among the often asked, through the
// question in it asked most recently, ranked by its similar askings, then likes, then a common
// question written fresh before a single asking. In each, questions this browser has not seen
// come first, then older ones, then the last set, so a refresh shows others while the pool allows.
// One per topic while topics remain; one question asked two ways shows once. The same rules as
// lizheng.ai's homepage, which tests them.
export function pickDiscovery(pool, seen, count = 4) {
  const cards = foldDiscovery(pool);
  const before = new Set(seen), last = new Set(seen.slice(-count));
  const visit = card => last.has(card.public_id) ? 2 : before.has(card.public_id) ? 1 : 0;
  const topic = card => card.topic_key || card.public_id;
  const often = (a, b) => b.similar_count - a.similar_count || b.likes - a.likes
    || Number(!!askedTime(a)) - Number(!!askedTime(b)) || Date.parse(b.published_at) - Date.parse(a.published_at);
  const latest = new Map();
  for (const card of cards) if (!latest.has(topic(card)) || askedTime(card) > askedTime(latest.get(topic(card)))) latest.set(topic(card), card);
  const lists = {
    fresh: cards.filter(askedTime).sort((a, b) => visit(a) - visit(b) || askedTime(b) - askedTime(a)),
    common: [...latest.values()].sort((a, b) => visit(a) - visit(b) || often(a, b)),
  };
  // Once the topics run out, the rest in the same order, so a small pool still fills the list.
  const rest = [...cards].sort((a, b) => visit(a) - visit(b) || often(a, b));
  const picked = [], topics = new Set();
  const add = (card, role) => { picked.push({...card, role}); topics.add(topic(card)); return true; };
  const free = card => !picked.some(other => other.public_id === card.public_id);
  const take = role => { const card = lists[role].find(card => free(card) && !topics.has(topic(card))); return !!card && add(card, role); };
  const fill = () => { const card = rest.find(free); return !!card && add(card, askedTime(card) ? 'fresh' : 'common'); };
  while (picked.length < count) {
    const [role, other] = picked.length % 2 ? ['common', 'fresh'] : ['fresh', 'common'];
    if (!(take(role) || take(other) || fill())) break;
  }
  return picked;
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
