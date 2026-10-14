// 分享这条回答. An answered question has its own public page at ask.lizheng.ai/s/<date>/<word>,
// made by lizheng.ai as it archives the answer (shared/ask-share-link.ts) and sent with it
// (`share.url`, since 2026-10-14); the page stays. Each answered result also carries the record, its
// address word and Builder's proof, which only this page holds: with them it makes the page of an
// answer about the asker's own situation when asked to, and reports the share, which may give back
// one of today's questions (never inside WeChat, which forbids rewarding shares, or the iPhone app).
export const IN_WECHAT = typeof navigator !== 'undefined' && /MicroMessenger/i.test(navigator.userAgent);

async function post(path, body) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    credentials: 'same-origin', cache: 'no-store'});
  const value = await response.json().catch(() => null);
  if (!response.ok || !value) throw Object.assign(new Error('share failed'), {code: value?.code});
  return value;
}

const PAGE = /^https:\/\/ask\.lizheng\.ai\/s\/\d{4}-\d{2}-\d{2}\/[a-z0-9]{1,40}(?:-[a-z0-9]{1,40}){0,4}$/;
/** Whether an address is an answer's own page. */
export const isAnswerPage = url => typeof url === 'string' && PAGE.test(url);
/** The answer's page as Builder sent it with the answer, or '' (from Builder before 2026-10-14, or one not made). */
export const answerPage = share => (isAnswerPage(share?.url) ? share.url : '');

/** Makes (or finds) the answer's page; `bonus` asks for today's question back. `auto` is the page
 * made as the answer arrives, before anyone shares it: lizheng.ai does not count it as a share. */
export async function createShareLink(share, {surface, bonus, auto = false}) {
  const value = await post('/api/ask-lizheng/share', {record_id: share.record_id, word: share.word, proof: share.proof, surface, bonus, auto});
  if (!isAnswerPage(value.url)) throw new Error('share failed');
  return value;
}

/** Copies text that is still on its way. Safari lets a page write the clipboard only while the
 * click lasts, so the write starts now and waits for the text; elsewhere it is written when it comes. */
export async function copyWhenReady(text) {
  try {
    if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItem({'text/plain': Promise.resolve(text).then(value => new Blob([value], {type: 'text/plain'}))})]);
      return true;
    }
  } catch { /* Fall back to a plain write below. */ }
  try { await navigator.clipboard.writeText(await text); return true; } catch { return false; }
}

/** The address as people read it: without https://. */
// A public question's page ends in its long id: it shows by the id's first part (the link stays whole).
export const shareDisplay = url => url.replace(/^https:\/\//, '').replace(/(\/(?:ask|q)\/[0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, '$1…');
