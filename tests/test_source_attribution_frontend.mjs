import test from 'node:test';
import assert from 'node:assert/strict';
import {discoveryDetail} from '../src/discovery.js';
import {sourceAttributionLabel} from '../src/source-attribution.js';
import {sourceCopyText} from '../src/source-access.js';

const synthetic = {
  id:'S1', title:'Synthetic fixture', url:'https://example.org/fixture',
  data_layer:'raw', speaker_id:'guest:fixture', speaker_name:'Synthetic speaker', speaker_status:'root-reviewed',
  speaker_classification:'mixed-or-unresolved', fragment_id:'fixture#turn-1', source_snapshot_sha256:'a'.repeat(64),
  participant_names:'Synthetic speaker', participant_entity_ids:'guest:fixture; guest:other',
  participant_aliases:'Fixture; Example', participant_metadata_basis:'reviewed-public-metadata',
  speech_act:'substantive', content_origin:'speaker-attributed-speech', generation_method:'source-original',
  transcript_quality:'ai-text-reviewed',
};
async function detail(sources) {
  const prior = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/ask-lizheng/discovery/detail?public_id=fixture');
    assert.equal(options.credentials, 'omit');
    assert.equal(options.cache, 'no-store');
    return {ok:true, json:async () => ({question:'Synthetic question', answer:{summary:'Synthetic summary', sections:[], sources, limitations:'Synthetic limit'}})};
  };
  try {return await discoveryDetail('fixture');} finally {globalThis.fetch = prior;}
}
test('discovery cache retains every declared attribution field byte-for-byte', async () => {
  const actual = (await detail([synthetic])).answer.sources[0];
  for (const field of ['data_layer','speaker_id','speaker_name','speaker_status','speaker_classification','fragment_id','source_snapshot_sha256',
    'participant_names','participant_entity_ids','participant_aliases','participant_metadata_basis','speech_act','content_origin','generation_method']) {
    assert.equal(actual[field], synthetic[field], field);
  }
});
test('discovery ignores undeclared fields and non-string attribution values', async () => {
  const actual = (await detail([{...synthetic, internal_only:'excluded', participant_entity_ids:['guest:fixture'], participant_aliases:null, speech_act:42}])).answer.sources[0];
  for (const field of ['internal_only','participant_entity_ids','participant_aliases','speech_act']) assert.equal(Object.hasOwn(actual, field), false);
  assert.equal(actual.speaker_id, synthetic.speaker_id);
});
test('invalid discovery source identity or URL is rejected', async () => {
  const actual = (await detail([{...synthetic,id:'not-source'},{...synthetic,url:'http://example.org/fixture'}])).answer.sources;
  assert.equal(actual.length, 0);
});
test('named raw speaker label and copy agree after discovery sanitizing', async () => {
  const actual = (await detail([synthetic])).answer.sources[0];
  assert.equal(sourceAttributionLabel(actual), '发言：Synthetic speaker');
  assert.ok(sourceCopyText(actual).includes('发言：Synthetic speaker'));
  assert.ok(sourceCopyText(actual).includes('未逐句听校'));
});
test('unknown raw speaker remains unknown in label and copy', () => {
  const actual = {...synthetic,speaker_id:'unknown',speaker_name:''};
  assert.equal(sourceAttributionLabel(actual), '说话人待核');
  assert.ok(sourceCopyText(actual).includes('说话人待核'));
  assert.equal(sourceCopyText(actual).includes('发言：'), false);
});
test('derived AI attribution wins over an inherited named speaker', () => {
  const actual = {...synthetic,data_layer:'derived',author:'AI',speaker_id:'yuzheng',speaker_name:'Synthetic author'};
  assert.equal(sourceAttributionLabel(actual), 'AI整理');
  assert.ok(sourceCopyText(actual).includes('AI整理'));
  assert.equal(sourceCopyText(actual).includes('发言：'), false);
});
test('derived publisher adaptation uses existing presentation label', () => {
  const actual = {...synthetic,data_layer:'derived',author:'Synthetic publisher',content_origin:'published-editorial-adaptation',generation_method:'source-declared-adaptation'};
  assert.equal(sourceAttributionLabel(actual), '整理稿');
  assert.ok(sourceCopyText(actual).includes('整理稿'));
});
test('legacy mixed classification retains unresolved label without inventing a speaker', () => {
  const actual = {id:'S1',title:'Synthetic legacy',url:'https://example.org/legacy',speaker_classification:'mixed-or-unresolved'};
  assert.equal(sourceAttributionLabel(actual), '多人发言 · 说话人待核');
  assert.ok(sourceCopyText(actual).includes('多人发言 · 说话人待核'));
  assert.equal(sourceAttributionLabel({source_type:'community-post'}), '');
});
