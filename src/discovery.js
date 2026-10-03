// Published, de-identified questions people asked, curated in the separate Ops service and
// served by lizheng.ai on this host. Anywhere else the reads fail quietly and the page keeps its examples.

// The pool a visit draws from: the most asked topics (one question each) and the newest questions.
const VIEWS = ['window=all&sort=frequent&limit=20', 'window=all&sort=recent&limit=20'];
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

async function list(view, signal) {
  try {
    const response = await fetch(`/api/ask-lizheng/discovery/questions?${view}`, {cache: 'no-store', credentials: 'omit', signal});
    if (!response.ok) return [];
    const value = await response.json();
    return Array.isArray(value?.items) ? value.items.filter(card) : [];
  } catch { return []; }
}

/** The whole pool, newest first, a page at a time. An expired cursor means: start again. */
export async function discoveryPage(cursor) {
  try {
    const response = await fetch(`/api/ask-lizheng/discovery/questions?window=all&sort=recent&limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
      {cache: 'no-store', credentials: 'omit'});
    if (response.status === 409) return {expired: true, items: [], next: null};
    if (!response.ok) return null;
    const value = await response.json();
    const next = typeof value?.next_cursor === 'string' && /^[A-Za-z0-9_-]{1,512}$/.test(value.next_cursor) ? value.next_cursor : null;
    return {expired: false, items: Array.isArray(value?.items) ? value.items.filter(card) : [], next};
  } catch { return null; }
}

export async function discoveryPool(signal) {
  const pool = new Map();
  for (const item of (await Promise.all(VIEWS.map(view => list(view, signal)))).flat()) {
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

// The questions a visit shows. A first visit gets the most asked topics. Later visits put unseen
// questions first, then older ones, then the last set, each group in a random order that leans
// toward common topics, so a refresh shows something else while the pool allows. One per topic
// where possible. The same rules as lizheng.ai's homepage, which tests them.
export function pickDiscovery(pool, seen, count = 4, random = Math.random) {
  const weight = item => 1 + Math.log2(1 + item.topic_question_count);
  const shuffled = items => items.map(item => ({item, key: random() ** (1 / weight(item))}))
    .sort((a, b) => b.key - a.key).map(entry => entry.item);
  let order;
  if (!seen.length) order = [...pool].sort((a, b) => b.topic_question_count - a.topic_question_count);
  else {
    const before = new Set(seen), last = new Set(seen.slice(-count));
    order = [
      ...shuffled(pool.filter(item => !before.has(item.public_id))),
      ...shuffled(pool.filter(item => before.has(item.public_id) && !last.has(item.public_id))),
      ...shuffled(pool.filter(item => last.has(item.public_id))),
    ];
  }
  const picked = [], topics = new Set();
  for (const item of order) {
    const topic = item.topic_key || item.public_id;
    if (picked.length < count && !topics.has(topic)) { picked.push(item); topics.add(topic); }
  }
  for (const item of order) if (picked.length < count && !picked.includes(item)) picked.push(item);
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
