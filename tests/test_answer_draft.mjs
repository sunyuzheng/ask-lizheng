import test from 'node:test';
import assert from 'node:assert/strict';
import {afterPartial, afterProgress} from '../src/answer-draft.js';

// A turn as the page holds it, taking the answer service's events in order.
const take = (turn, [name, value]) => ({...turn, ...(name === 'partial' ? afterPartial(turn, value) : afterProgress(value))});
const source = (id, extra = {}) => ({id, title: `材料${id}`, url: `https://example.com/${id}`, ...extra});
const section = {heading: '先核对条件', body: '比较目标与实际结果。[S2]', source_ids: ['S2'], kind: 'synthesis'};

test('the summary shows first, then its sections; each partial is the whole draft so far', () => {
  let turn = {previewSources: [source('S1'), source('S2')]};
  turn = take(turn, ['progress', {stage: 'drafting', message: '回答内容已开始返回…'}]);
  assert.equal(turn.partial, undefined);
  turn = take(turn, ['partial', {summary: '先用一个小任务验证判断[S3]。', sections: [], sources: [source('S3')]}]);
  assert.deepEqual([turn.partial.summary, turn.partial.sections], ['先用一个小任务验证判断[S3]。', []]);
  // The source its mark points to joins the candidates by number, without losing or repeating any.
  assert.deepEqual(turn.previewSources.map(item => item.id), ['S1', 'S2', 'S3']);
  turn = take(turn, ['partial', {summary: '先用一个小任务验证判断[S3]。', sections: [section], sources: [source('S2', {reason: '可核对'}), source('S3')]}]);
  assert.deepEqual([turn.partial.summary, turn.partial.sections.length], ['先用一个小任务验证判断[S3]。', 1]);
  assert.deepEqual(turn.previewSources.map(item => [item.id, item.reason]), [['S1', undefined], ['S2', '可核对'], ['S3', undefined]]);
});

test('a service that sends no summary early still shows its sections', () => {
  const turn = take({}, ['partial', {sections: [section], sources: [source('S2')]}]);
  assert.equal(turn.partial.summary, undefined);
  assert.equal(turn.partial.sections.length, 1);
});

test('repairing withdraws the summary with the sections; the repaired draft replaces them', () => {
  let turn = take({previewSources: [source('S1')]}, ['partial', {summary: '第一次的结论。', sections: [section], sources: [source('S2')]}]);
  turn = take(turn, ['progress', {stage: 'repairing', message: '回答中的来源标注或格式需要修正，正在重新整理…'}]);
  assert.equal(turn.partial, undefined);
  assert.equal(turn.progress.stage, 'repairing');
  // The materials found stay readable.
  assert.deepEqual(turn.previewSources.map(item => item.id), ['S1', 'S2']);
  turn = take(turn, ['partial', {summary: '修正后的结论。', sections: [], sources: []}]);
  assert.deepEqual(turn.partial, {summary: '修正后的结论。', sections: [], sources: []});
  // Any other stage leaves the draft as it is.
  turn = take(turn, ['progress', {stage: 'checking', message: '回答已生成，正在核对来源编号和输出格式…'}]);
  assert.equal(turn.partial.summary, '修正后的结论。');
});
