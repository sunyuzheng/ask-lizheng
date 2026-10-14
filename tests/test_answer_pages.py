"""Each answered question's own page (2026-10-14): the page links to it as Builder sends it, and a
question shown in 别人在问什么 links to its own page, never to another address. Synthetic data only."""
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def test_the_page_links_each_answer_and_each_public_question_to_its_own_page():
    code = r'''
import assert from 'node:assert/strict';
import {answerPage, isAnswerPage} from './src/share-link.js';
import {discoveryDetail, discoveryLink, discoveryLists} from './src/discovery.js';
const id = '01234567-89ab-4cde-8f01-23456789abcd', page = 'https://ask.lizheng.ai/s/2026-10-14/career-choice-2';
const share = {record_id: id, word: 'career-choice', proof: 'f'.repeat(64)};
assert.equal(answerPage({...share, url: page}), page);
assert.equal(answerPage(share), '');
for (const url of ['https://example.test/s/2026-10-14/career-choice', 'https://ask.lizheng.ai/s/2026-10-14/Career', 'https://ask.lizheng.ai/q/' + id, 7]) {
  assert.equal(isAnswerPage(url), false);
  assert.equal(answerPage({...share, url}), '');
  assert.equal(discoveryLink(id, url), 'https://ask.lizheng.ai/q/' + id);
}
assert.equal(discoveryLink(id, page), page);
assert.equal(discoveryLink(id), 'https://ask.lizheng.ai/q/' + id);
globalThis.fetch = async () => Response.json({question: 'Synthetic public question', share_url: page,
  answer: {summary: 'Synthetic summary', sections: [], sources: []}});
assert.equal((await discoveryDetail(id)).share_url, page);
globalThis.fetch = async () => Response.json({question: 'Synthetic public question', share_url: 'https://example.test/',
  answer: {summary: 'Synthetic summary', sections: [], sources: []}});
assert.equal((await discoveryDetail(id)).share_url, undefined);
const card = {public_id: id, revision: 1, topic_key: 't', topic_label: '主题', question: '合成问题？', summary: '', published_at: '2026-10-14T00:00:00Z',
  topic_question_count: 1, likes: 0, share_url: page};
const other = n => ({public_id: id.slice(0, -1) + n, revision: 1, question: '合成' + n, published_at: '2026-10-14T00:00:00Z', likes: 0});
globalThis.fetch = async () => Response.json({recent: [card], frequent: [{...card, similar: [{...other(1), share_url: page}, {...other(2), share_url: 'https://example.test/'}]}]});
const lists = await discoveryLists();
assert.equal(lists.recent[0].share_url, page);
assert.deepEqual(lists.frequent[0].similar.map(item => item.share_url), [page, undefined]);
console.log('answer pages passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", code], cwd=PROJECT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "answer pages passed"
