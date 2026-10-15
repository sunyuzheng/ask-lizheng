import test from 'node:test';
import assert from 'node:assert/strict';
import {homeQuestion, HOME_QUESTION_MAX} from '../src/home-question.js';

const fragment = (question, mode) => '#q=' + encodeURIComponent(question) + (mode ? '&mode=' + mode : '');

test('the homepage box hands its question over and it is sent', () => {
  assert.deepEqual(homeQuestion(fragment('AI时代怎么学习？'), 'https://www.lizheng.ai/'),
    {question: 'AI时代怎么学习？', mode: 'ask', send: true});
  assert.deepEqual(homeQuestion(fragment('先读哪几篇 & 为什么=', 'find'), 'https://lizheng.ai/en'),
    {question: '先读哪几篇 & 为什么=', mode: 'find', send: true});
});

test('from anywhere else the question only fills the box', () => {
  for (const referrer of ['', 'https://example.com/', 'http://www.lizheng.ai/', 'https://ask.lizheng.ai/',
    'https://www.lizheng.ai.example.com/', 'https://city.lizheng.ai/', 'not a url'])
    assert.equal(homeQuestion(fragment('一个问题'), referrer).send, false, referrer);
});

test('only #q= carries a question, and never an empty or overlong one', () => {
  for (const hash of ['', '#recent', '#frequent', '#q=', '#q=%20%20', '#mode=find', undefined])
    assert.equal(homeQuestion(hash, 'https://www.lizheng.ai/'), null, String(hash));
  const long = homeQuestion(fragment('问'.repeat(HOME_QUESTION_MAX + 50)), 'https://www.lizheng.ai/');
  assert.equal(long.question.length, HOME_QUESTION_MAX);
  assert.equal(homeQuestion(fragment('  留白  ', 'other'), '').question, '留白');
  assert.equal(homeQuestion(fragment('一个问题', 'other'), '').mode, 'ask');
});
