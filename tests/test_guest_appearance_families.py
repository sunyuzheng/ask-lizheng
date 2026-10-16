"""Synthetic incoming-release contracts; no real corpus or model calls.

ASK_TEST_CONTEXT_ROOT selects the candidate public parser. A previously pinned
parser without external-appearance support is left untouched and skips this
incoming contract until its normal release update supplies that capability.
"""
import copy
import json

import pytest

from server.retrieval import ContextIndex, search_module
from server.semantic import SemanticIndexError, prepare_index
from test_backend import context_pack, source_file
from test_semantic import write_manifest


def appearance_versions(root):
    if not hasattr(search_module(root),'appearance_transcript_bindings'):
        pytest.skip('Pinned parser predates external appearance bindings; test the candidate via ASK_TEST_CONTEXT_ROOT')
    identity='appearance-synthetic-versions';youtube='youtube-ABCDEFGHIJK'
    yt_url='https://www.youtube.com/watch?v=ABCDEFGHIJK';external='dialogue-synthetic-external'
    source_file(root,'corpus/videos','synthetic-family.md',{
        'id':youtube,'title':'合成版本家族探针','source_type':'video-transcript',
        'source_url':yt_url,'source_family':yt_url,'published_at':'2026-09-25',
        'data_layer':'raw','speaker_id':'unknown','speaker_status':'unresolved',
        'evidence_role':'speaker-attributed-speech','content_origin':'mixed-or-unresolved-speech',
        'yuzheng_stance_weight':'not-evidence','license':'LicenseRef-Lizheng-Reference-Use-1.0',
        'rights_scope':'first-party','source_visibility':'members-only','text_access':'public','timestamp_mode':'youtube',
    },f'[00:02:03]({yt_url}&t=123s) 合成版本家族探针，同一次录音的原始版本。')
    folder=root/'corpus/dialogues';folder.mkdir(parents=True)
    data={'schema_version':1,'data_layer':'raw','id':external,'title':'合成版本家族探针',
        'source_type':'audio-transcript','source_url':'https://audio.example.test/episode/version',
        'source_family':'https://audio.example.test/episode/version','published_at':'2026-09-25',
        'timestamp_mode':'display-only','license':'LicenseRef-Original-Rights-Retained','rights_scope':'third-party-reference',
        'source_visibility':'public','text_access':'public','content_origin':'mixed-or-unresolved-speech',
        'evidence_role':'speaker-attributed-speech','yuzheng_stance_weight':'not-evidence',
        'units':[{'id':'cue-1','text':'合成版本家族探针，同一次录音的外部原始版本。','start_seconds':61.5,'end_seconds':64,
                  'speaker_id':'unknown','speaker_name':'','speaker_status':'unresolved'}]}
    (folder/(external+'.json')).write_text(json.dumps(data))
    row={'id':identity,'title':'合成版本家族探针目录','show':'Synthetic show','url':'https://show.example.test/episode',
         'published_at':'2026-09-25','transcript_id':external,'transcript_path':f'corpus/dialogues/{external}.json',
         'source_versions':[{'transcript_id':youtube},{'transcript_id':external}],
         'participants':[{'speaker_id':'yuzheng','speaker_name':'立正'},{'speaker_id':'host:synthetic','speaker_name':'Synthetic host'}]}
    (root/'catalog/guest-appearances.jsonl').write_text(json.dumps(row)+'\n')
    return row,data,youtube,external


def test_actual_store_and_retrieval_share_appearance_family_and_preserve_versions(context_pack):
    row,data,youtube,external=appearance_versions(context_pack)
    index=ContextIndex(context_pack);index.load()
    docs=[d for d in index.documents if d.source_id in {row['id'],youtube,external}]
    assert len(docs)==3 and {d.source_family for d in docs}=={row['id']}
    raw=[d for d in docs if d.data_layer=='raw']
    assert {d.source_id for d in raw}=={youtube,external}
    yt=next(d for d in raw if d.source_id==youtube);audio=next(d for d in raw if d.source_id==external)
    assert yt.source_url=='https://www.youtube.com/watch?v=ABCDEFGHIJK&t=123s'
    assert (yt.source_visibility,yt.text_access,yt.license)==('members-only','public','LicenseRef-Lizheng-Reference-Use-1.0')
    assert (audio.source_url,audio.timestamp_mode,audio.license)==(data['source_url'],'display-only',data['license'])
    assert all(d.speaker_id=='unknown' and d.yuzheng_stance_weight=='not-evidence' for d in raw)
    assert {index.briefs[i].family for i,d in enumerate(index.documents) if d.source_id in {row['id'],youtube,external}}=={row['id']}
    passages=[p for p in index.retrieve('合成版本家族探针') if p.evidence['source_family']==row['id']]
    assert len(passages)==1
    assert passages[0].source['data_layer']=='raw'


def test_distinct_appearance_stays_independent_and_duplicate_binding_fails(context_pack):
    row,data,youtube,external=appearance_versions(context_pack)
    other=copy.deepcopy(data);other['id']='dialogue-synthetic-independent'
    (context_pack/f"corpus/dialogues/{other['id']}.json").write_text(json.dumps(other))
    other_row={**row,'id':'appearance-independent','transcript_id':other['id'],
               'transcript_path':f"corpus/dialogues/{other['id']}.json",'source_versions':[]}
    catalog=context_pack/'catalog/guest-appearances.jsonl'
    catalog.write_text(json.dumps(row)+'\n'+json.dumps(other_row)+'\n')
    index=ContextIndex(context_pack);index.load()
    families={p.evidence['source_family'] for p in index.retrieve('合成版本家族探针')}
    assert {row['id'],other_row['id']}<=families
    other_row['source_versions']=[{'transcript_id':youtube}]
    catalog.write_text(json.dumps(row)+'\n'+json.dumps(other_row)+'\n')
    with pytest.raises(ValueError,match='appearance_conflict'):ContextIndex(context_pack).load()


def test_canonical_semantic_projection_binds_family_for_both_source_formats(context_pack):
    row,data,youtube,external=appearance_versions(context_pack)
    index=ContextIndex(context_pack);index.load()
    docs=[d for d in index.documents if d.source_id in {youtube,external}]
    write_manifest(context_pack)
    metadata,inputs=prepare_index(context_pack,docs)
    assert len(metadata['entries'])==len(inputs)==2
    for doc in docs:
        original=doc.source_family;doc.source_family=doc.source_url
        with pytest.raises(SemanticIndexError,match='attribution differs'):prepare_index(context_pack,docs)
        doc.source_family=original
