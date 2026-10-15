// 反馈问题 (2026-10-15, the user: everyone needs a quick way to report a bug, above all while an
// answer is slow). A short note mailed to the owner, with what the page saw: the question (never
// the situation), how long it waited, the stage, the error. lizheng.ai checks and mails it
// (shared/ask-report.ts there, which also limits how many a network and a day may send). This file
// is copied there as shared/ask-report-client.js by its scripts/sync-ask-app.mjs, so the homepage
// section sends and says the same.
export const REPORT_ENDPOINT = '/api/ask-lizheng/report';
export const REPORT_TEXT_MAX = 800;
const QUESTION_MAX = 300;
const CODE = /^[a-z][a-z_-]{0,47}$/;
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$/;
const SHARE = /^https:\/\/ask\.lizheng\.ai\/s\/\d{4}-\d{2}-\d{2}\/[a-z0-9]+(?:-[a-z0-9]+){0,4}$/;

export const REPORT_COPY = {
  zh: {
    button: '反馈问题',
    title: '反馈问题',
    introTurn: '会把这次的情况直接发给立正。可以补一句遇到了什么，不写也能发。',
    introPage: '说说遇到了什么，会直接发给立正。',
    label: '遇到了什么',
    placeholder: '比如：一直在转圈，没出回答。需要回复的话，可以留个邮箱或微信。',
    withTurn: '一起发送：这个问题、等了多久、出错信息和设备类型。不发送你填的处境。',
    withPage: '一起发送：设备和浏览器类型。',
    send: '发送',
    sending: '正在发送…',
    cancel: '取消',
    close: '关闭',
    sent: '收到了，谢谢！',
    failed: '没发出去，可以稍后再试，或者去社区帖子留言。',
    limited: '今天的反馈有点多，暂时发不出去了，可以去社区帖子留言。',
    community: '想公开讨论，或举报某条问答：',
    communityLink: '去社区帖子留言',
  },
  en: {
    button: 'Report a problem',
    title: 'Report a problem',
    introTurn: 'This sends what happened straight to Yuzheng. Add a line if you like, or just send it.',
    introPage: 'Tell us what went wrong. It goes straight to Yuzheng.',
    label: 'What happened',
    placeholder: 'For example: it kept spinning and no answer came. Leave an email if you want a reply.',
    withTurn: 'Sent with it: this question, how long it took, any error, and your device type. Your situation is not sent.',
    withPage: 'Sent with it: your device and browser type.',
    send: 'Send',
    sending: 'Sending…',
    cancel: 'Cancel',
    close: 'Close',
    sent: 'Got it, thank you!',
    failed: 'It didn’t go through. Please try again later, or leave a comment on the community post.',
    limited: 'There have been many reports today, so this one can’t be sent. Please leave a comment on the community post.',
    community: 'To discuss in public or report a Q&A: ',
    communityLink: 'comment on the community post',
  },
};

const clip = (value, max) => (typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : undefined);
const whole = value => (Number.isFinite(value) ? Math.min(3600, Math.max(0, Math.round(value))) : undefined);
const matching = (value, pattern) => (typeof value === 'string' && pattern.test(value) ? value : undefined);
const iso = value => {
  const time = typeof value === 'number' ? value : Date.parse(value);
  return Number.isFinite(time) && time > 0 ? new Date(time).toISOString() : undefined;
};

/**
 * What one report sends. `place` is waiting | failed | answer | page; a turn (anything but page)
 * may carry what the page saw of that question. The situation is never a field.
 */
export function reportPayload({surface, place, text = '', turn = {}}) {
  const payload = {v: 1, surface, place, text: String(text).trim().slice(0, REPORT_TEXT_MAX)};
  if (place === 'page') return payload;
  const seen = {
    question: clip(turn.question, QUESTION_MAX),
    asked_at: iso(turn.askedAt),
    elapsed: whole(turn.elapsed),
    quiet: whole(turn.quiet),
    stage: matching(turn.stage, CODE),
    error: matching(turn.error, CODE),
    status: matching(turn.status, CODE),
    model: matching(turn.model, MODEL),
    share: matching(turn.share, SHARE),
  };
  for (const [key, value] of Object.entries(seen)) if (value !== undefined) payload[key] = value;
  return payload;
}

/** Sends one report: 'sent', 'limited' (today's are used up) or 'failed'. */
export async function sendReport(payload) {
  try {
    const response = await fetch(REPORT_ENDPOINT, {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload), credentials: 'same-origin', cache: 'no-store'});
    return response.ok ? 'sent' : response.status === 429 ? 'limited' : 'failed';
  } catch {
    return 'failed';
  }
}
