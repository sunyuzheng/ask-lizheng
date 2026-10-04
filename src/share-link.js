// 分享这条回答: the person who asked gives one answered question its own public page at
// ask.lizheng.ai/s/<date>/<word>, made by lizheng.ai (shared/ask-share-link.ts); the page stays.
// Each answered result carries `share`: the record, its address word and Builder's proof, which
// only this page holds. A share may give back one of today's questions; the page never offers that
// inside WeChat, which forbids rewarding shares, or in the iPhone app.
export const IN_WECHAT = typeof navigator !== 'undefined' && /MicroMessenger/i.test(navigator.userAgent);

async function post(path, body) {
  const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    credentials: 'same-origin', cache: 'no-store'});
  const value = await response.json().catch(() => null);
  if (!response.ok || !value) throw Object.assign(new Error('share failed'), {code: value?.code});
  return value;
}

/** Makes (or finds) the answer's page; `bonus` asks for today's question back. */
export async function createShareLink(share, {surface, bonus}) {
  const value = await post('/api/ask-lizheng/share', {record_id: share.record_id, word: share.word, proof: share.proof, surface, bonus});
  if (typeof value.url !== 'string' || !value.url.startsWith('https://ask.lizheng.ai/s/')) throw new Error('share failed');
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
export const shareDisplay = url => url.replace(/^https:\/\//, '');
