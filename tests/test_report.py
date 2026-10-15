"""反馈问题 (2026-10-15): from the waiting card, a failed or stopped answer, an answer and the start
page's footer, a short note goes to lizheng.ai, which mails it to the owner (its shared/ask-report.ts
checks the same shape). The note carries what the page saw of the question, never the situation.
Synthetic data only."""
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def test_a_report_carries_what_the_page_saw_but_never_the_situation():
    code = r'''
import assert from 'node:assert/strict';
import {REPORT_COPY, REPORT_ENDPOINT, reportCopy, reportPayload, sendReport} from './src/report.js';
const turn = {question: '  合成问题？  ', askedAt: Date.parse('2026-10-15T05:38:57Z'), elapsed: 63.4, quiet: 40.6, stage: 'thinking',
  error: 'provider_timeout', status: 'sources-only', model: 'deepseek-v4-flash', share: 'https://ask.lizheng.ai/s/2026-10-15/quiet-river',
  context: '合成处境', situation: '合成处境'};
assert.deepEqual(reportPayload({surface: 'ask', place: 'waiting', text: '  一直在转圈 ', turn}), {
  v: 1, surface: 'ask', place: 'waiting', text: '一直在转圈', question: '合成问题？', asked_at: '2026-10-15T05:38:57.000Z',
  elapsed: 63, quiet: 41, stage: 'thinking', error: 'provider_timeout', status: 'sources-only', model: 'deepseek-v4-flash',
  share: 'https://ask.lizheng.ai/s/2026-10-15/quiet-river'});
// What does not fit the shape lizheng.ai accepts is left out rather than sent.
assert.deepEqual(reportPayload({surface: 'app', place: 'failed', turn: {question: '问'.repeat(400), askedAt: '2026-10-15T05:38:57Z',
  elapsed: 99999, stage: 'Not A Code', share: 'https://example.test/s/2026-10-15/x', model: '../x'}}),
  {v: 1, surface: 'app', place: 'failed', text: '', question: '问'.repeat(300), asked_at: '2026-10-15T05:38:57.000Z', elapsed: 3600});
// The page itself: only what was written.
assert.deepEqual(reportPayload({surface: 'home', place: 'page', text: '按钮点不动', turn}), {v: 1, surface: 'home', place: 'page', text: '按钮点不动'});
assert.equal(reportPayload({surface: 'ask', place: 'page', text: 'x'.repeat(900)}).text.length, 800);
const sent = [];
for (const [status, expected] of [[204, 'sent'], [429, 'limited'], [503, 'failed']]) {
  globalThis.fetch = async (url, options) => { sent.push([url, options]); return new Response(null, {status}); };
  assert.equal(await sendReport({v: 1}), expected);
}
globalThis.fetch = async () => { throw new TypeError('offline'); };
assert.equal(await sendReport({v: 1}), 'failed');
assert.equal(sent[0][0], REPORT_ENDPOINT);
assert.equal(sent[0][1].method, 'POST');
assert.equal(sent[0][1].credentials, 'same-origin');
assert.equal(sent[0][1].headers['Content-Type'], 'application/json');
assert.equal(REPORT_COPY.zh.button, '反馈问题');
for (const lang of ['zh', 'en']) assert.deepEqual(Object.keys(REPORT_COPY[lang]), Object.keys(REPORT_COPY.zh));
// Each page in its own voice: the ask page names 立正, the homepage speaks as him.
assert.equal(reportCopy('zh', '立正').introPage, '说说遇到了什么，会直接发给立正。');
assert.equal(reportCopy('zh', '我').introTurn, '会把这次的情况直接发给我。可以补一句遇到了什么，不写也能发。');
assert.equal(reportCopy('en', 'me').introPage, 'Say what went wrong. It goes straight to me.');
for (const words of [reportCopy('zh', '立正'), reportCopy('en', 'Yuzheng')]) assert.ok(Object.values(words).every(text => !/[{}]/.test(text)));
console.log('report passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", code], cwd=PROJECT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "report passed"


def test_the_report_is_at_hand_while_waiting_after_a_failure_under_an_answer_and_in_the_footer():
    page = (PROJECT / "src" / "main.jsx").read_text()
    for opener in ("onReport={() => openReport('waiting', m)}", "openReport('failed', m)", "openReport('answer', m)", "openReport('page')"):
        assert opener in page, opener
    # The waiting card puts it beside 停止, so it is there while the answer is slow.
    working = page[page.index("function Working("):page.index("function Working(") + 900]
    assert working.index("onClick={onReport}") < working.index("onClick={onStop}")
    # The situation is not part of what the page saw.
    opener = page[page.index("const openReport = "):page.index("setReporting({place, turn});")]
    assert "context" not in opener and "situation" not in opener
