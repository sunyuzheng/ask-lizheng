// A question asked in the homepage's box (2026-10-15). Since then the homepage only takes the question
// and opens this page with it in the fragment, #q=<question> (and &mode=find for 从哪读起), which no
// server sees or logs; everything else about asking happens here, so there is one asking page.
// It is sent at once, as if asked here, only when this page was opened from www.lizheng.ai, whose box
// shows the same notice; from anywhere else it only fills the box, as ?q= does, so a link can never
// ask for someone. Browsers send the origin of the page a link was followed from, and a page cannot
// make it another site's.
const HOME_HOSTS = ['www.lizheng.ai', 'lizheng.ai'];
export const HOME_QUESTION_MAX = 2000;

export function homeQuestion(hash, referrer) {
  if (typeof hash !== 'string' || !hash.startsWith('#q=')) return null;
  const params = new URLSearchParams(hash.slice(1));
  const question = (params.get('q') || '').trim().slice(0, HOME_QUESTION_MAX);
  if (!question) return null;
  let send = false;
  try {
    const from = new URL(referrer);
    send = from.protocol === 'https:' && HOME_HOSTS.includes(from.hostname);
  } catch { /* opened without a referrer: fill the box only */ }
  return {question, mode: params.get('mode') === 'find' ? 'find' : 'ask', send};
}
