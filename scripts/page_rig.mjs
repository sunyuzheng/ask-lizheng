// The page on this machine, without Builder, lizheng.ai or any credential: `npm run dev:rig`.
//
// The real page source, with what it talks to stood in for here:
// - /api/ask is a fake answer service that takes its time (ASK_RIG_WAIT_MS, 24 seconds by default),
//   sending the same events as Builder: progress, sources, the approach, the answer as it is written
//   (its summary first, then a section at a time), the result. A question with 失败 in it fails
//   part-way; one with 修正 has what was written withdrawn and written again, as when Builder finds
//   an answer invalid. The answer is one of the public answers, so it reads like a real one.
// - 别人在问什么 (lists, an opened answer) and the material map are read from production, GET only.
// - Usage counts, likes, shares and reports are answered here and never reach production, so trying
//   the page does not show up in the owner's statistics.
// - /s/<anything> opens the page as lizheng.ai serves someone's shared answer (#ask-shared).
// - ASK_RIG_ANSWERS=http://127.0.0.1:8000 hands /api/ask to an answer service started on this
//   machine instead (README「本地运行」; it needs a model token), for real answers at real speed. The
//   page is told that nothing is recorded (v1 notice), which is true of a service started that way.
//
// scripts/page_walkthrough.mjs walks the page on this rig and checks what a release must not break.
import {defineConfig, mergeConfig} from 'vite';
import base from '../vite.config.js';

const PROD = 'https://ask.lizheng.ai';
const WAIT = Number(process.env.ASK_RIG_WAIT_MS || 24000);
const ANSWERS = (process.env.ASK_RIG_ANSWERS || '').replace(/\/$/, '');
// What the page needs of /api/meta to let a question be sent, when production's cannot be read.
const META = {context_date: '', counts: {}, model: 'local-rig', mode: 'live', query_logging: {enabled: true, retention_days: 30},
  ops_logging: {enabled: true, retention: 'until_deleted', notice: 'v4', answer_archive: true, context_archive: false, public_display: 'as_asked'}};
const READ_FROM_PRODUCTION = new Set(['/api/ask-lizheng/discovery/lists', '/api/ask-lizheng/discovery/detail', '/api/answer-map']);

const json = (res, status, value) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
const read = async path => (await fetch(`${PROD}${path}`, {signal: AbortSignal.timeout(20000)})).json();
let meta = null, sample = null;
// A public answer to play back: one with a page of its own, so a shared page can be shown too.
async function publicAnswer() {
  if (sample) return sample;
  const lists = await read('/api/ask-lizheng/discovery/lists');
  const card = lists.recent.find(item => item.share_url) || lists.recent[0] || lists.frequent[0];
  const detail = await read(`/api/ask-lizheng/discovery/detail?public_id=${card.public_id}`);
  sample = {card, detail};
  return sample;
}

const rig = {
  name: 'ask-page-rig',
  async transformIndexHtml(html, ctx) {
    if (!/^\/s\//.test(ctx.originalUrl || '')) return html;
    const {card, detail} = await publicAnswer();
    const shared = {question: detail.question, url: card.share_url, answer: {status: 'answered', ...detail.answer}, turns: detail.turns || [], created_at: card.asked_at};
    return html.replace('<div id="root"></div>', `<div id="root"></div><script id="ask-shared" type="application/json">${JSON.stringify(shared).replace(/</g, '\\u003c')}</script>`);
  },
  configureServer(server) {
    server.middlewares.use(async (req, res, next) => {
      const url = new URL(req.url, 'http://rig');
      const path = url.pathname;
      if (!path.startsWith('/api/')) return next();
      try {
        if (path === '/api/meta') {
          meta ||= ANSWERS
            ? {...await (await fetch(`${ANSWERS}/api/meta`, {signal: AbortSignal.timeout(20000)})).json(), query_logging: {enabled: true, retention_days: 30}, ops_logging: {enabled: false}}
            : await read('/api/meta').catch(() => META);
          return json(res, 200, meta);
        }
        if (path === '/api/ask-lizheng/auth/session') return json(res, 200, {enabled: true, authenticated: false, founding: false, remaining: 3, login_ready: true, share_bonus: false});
        if (path === '/api/ask-lizheng/usage') { res.statusCode = 204; return res.end(); }
        if (READ_FROM_PRODUCTION.has(path) && req.method === 'GET') {
          const upstream = await fetch(`${PROD}${path}${url.search}`, {signal: AbortSignal.timeout(20000)});
          res.statusCode = upstream.status;
          res.setHeader('Content-Type', upstream.headers.get('content-type') || 'application/json');
          return res.end(Buffer.from(await upstream.arrayBuffer()));
        }
        if (path === '/api/ask' && req.method === 'POST') {
          const body = await new Promise(resolve => { let text = ''; req.on('data', chunk => { text += chunk; }); req.on('end', () => resolve(text)); });
          if (ANSWERS) {
            const upstream = await fetch(`${ANSWERS}/api/ask`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body});
            res.writeHead(upstream.status, {'Content-Type': upstream.headers.get('content-type') || 'application/json', 'Cache-Control': 'no-store'});
            for await (const chunk of upstream.body) res.write(chunk);
            return res.end();
          }
          const {detail: {answer}} = await publicAnswer();
          const asked = JSON.parse(body || '{}').question || '';
          const failing = /失败/.test(asked), repaired = /修正/.test(asked);
          res.writeHead(200, {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive'});
          const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
          const at = (share, step) => setTimeout(() => { if (!res.writableEnded) step(); }, WAIT * share);
          send('progress', {stage: 'retrieving', message: '正在查找相关公开材料…'});
          at(0.08, () => send('sources', {sources: answer.sources.slice(0, 4)}));
          at(0.12, () => send('progress', {stage: 'drafting', message: '正在根据找到的材料整理回答…'}));
          at(0.16, () => send('approach', {summary: '先找出材料里直接回应这个问题的判断，再看它成立的条件。', questions: ['这个判断在材料里是怎么说的？', '它在什么条件下成立？'], note: '这是整理方向，尚不是完整回答。'}));
          if (failing) { at(0.4, () => { send('error', {code: 'request_failed', status: 502}); res.end(); }); return undefined; }
          // The answer so far, as Builder sends it: the summary, the sections written, the sources they cite.
          const draft = count => {
            const sections = answer.sections.slice(0, count);
            const cited = new Set([...answer.summary.matchAll(/\[(S\d+)\]/g)].map(mark => mark[1]).concat(sections.flatMap(section => section.source_ids || [])));
            return {summary: answer.summary, sections, sources: answer.sources.filter(source => cited.has(source.id))};
          };
          at(0.3, () => send('partial', draft(0)));
          if (repaired) {
            at(0.4, () => send('partial', draft(1)));
            at(0.5, () => send('progress', {stage: 'repairing', message: '回答中的来源标注或格式需要修正，正在重新整理…'}));
            at(0.7, () => send('partial', draft(0)));
            at(0.85, () => send('partial', draft(1)));
          } else {
            at(0.55, () => send('partial', draft(1)));
            at(0.8, () => send('partial', draft(2)));
          }
          // No `share`: an answer made here has no page, and nothing asks lizheng.ai for one.
          at(1, () => { send('result', {status: 'answered', ...answer, followups: ['这个判断成立的条件是什么？', '如果我是刚起步的人，先做哪一步？']}); res.end(); });
          return undefined;
        }
        return json(res, 404, {code: 'not_on_the_rig'});
      } catch (error) { return json(res, 502, {code: 'rig_error', message: String(error)}); }
    });
  },
};

export default defineConfig(mergeConfig(base, {plugins: [rig], server: {proxy: {'/api': null, '/health': null}}}));
