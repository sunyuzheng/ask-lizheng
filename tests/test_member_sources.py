"""Synthetic regression for public text backed by members-only original videos."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from server.answers import assemble_answer, model_evidence, sources_only, SYSTEM_PROMPT
from server.app import create_app
from server.ops_records import archived_answer
from server.retrieval import ContextIndex, SOURCE_ACCESS_FIELDS
from test_backend import answer_for, context_pack, events, index, source_file

PROJECT = Path(__file__).resolve().parents[1]
JOIN = "https://www.youtube.com/channel/UC_5lJHgnMP_lb_VpIiXV0hQ/join"
ACCESS = {
    "source_visibility": "members-only", "text_access": "public", "membership_platform": "youtube",
    "membership_url": JOIN, "membership_verified_at": "2026-10-02",
    "transcript_source_kind": "previously-included-transcript", "transcript_quality": "uncorrected-asr",
    "speaker_classification": "mixed-or-unresolved",
}


def member_passage(index):
    # The original and its translation are one evidence family. This fixture
    # exercises the original video rather than whichever rendering ranks first.
    index.documents = [doc for doc in index.documents if doc.source_type != "video-translation"]
    index._texts = [doc.text.lower() for doc in index.documents]
    index._titles = [(doc.title + " " + doc.section).lower() for doc in index.documents]
    for doc in index.documents:
        if doc.source_type == "video-transcript":
            for key, value in ACCESS.items():
                setattr(doc, key, value)
            doc.content_origin = "mixed-or-unresolved-speech"
            doc.yuzheng_stance_weight = "not-evidence"
            doc.attribution_note = "Synthetic multi-speaker transcript; individual speakers retain their views."
            doc.author = "Synthetic speakers"
            doc.rights_scope = "publisher-authorized-transcript"
            doc.license = "LicenseRef-Original-Rights-Retained"
    return next(p for p in index.retrieve("职业选择能力作品") if p.source["source_type"] == "video-transcript")


def test_verified_access_is_preserved_without_reclassifying_attribution(index):
    passage = member_passage(index)
    assert {key: passage.source[key] for key in SOURCE_ACCESS_FIELDS} == ACCESS
    assert passage.source["author"] == "Synthetic speakers"
    assert passage.evidence["content_origin"] == "mixed-or-unresolved-speech"
    assert passage.evidence["yuzheng_stance_weight"] == "not-evidence"
    assert passage.source["public_copy_url"].endswith("corpus/videos/video.md")
    assert passage.source["url"].startswith("https://www.youtube.com/watch?")


def test_old_public_sources_gain_no_membership_label(index):
    passages = index.retrieve("职业选择")
    assert all(not set(SOURCE_ACCESS_FIELDS).intersection(p.source) for p in passages)


def test_markdown_parser_keeps_all_access_and_speaker_metadata(context_pack):
    source_file(context_pack, "corpus/videos", "member-mixed.md", {
        **ACCESS, "id": "synthetic-member-mixed", "title": "边界转移显微探针",
        "source_type": "video-transcript", "source_url": "https://www.youtube.com/watch?v=syntheticmixed",
        "published_at": "2026-10-02", "evidence_role": "speaker-attributed-speech",
        "source_family": "synthetic-mixed-family", "author": "Synthetic multiple speakers",
        "content_origin": "mixed-or-unresolved-speech", "yuzheng_stance_weight": "not-evidence",
        "rights_scope": "publisher-authorized-transcript", "license": "LicenseRef-Original-Rights-Retained",
        "attribution_note": "Synthetic mixed speakers retain their individual positions.",
    }, "[00:02:00](https://www.youtube.com/watch?v=syntheticmixed&t=120s) 边界转移显微探针的公开文字稿，发言不能全部归属于发布者。")
    parsed = ContextIndex(context_pack)
    parsed.load()
    document = next(doc for doc in parsed.documents if doc.source_id == "synthetic-member-mixed")
    assert {key: getattr(document, key) for key in SOURCE_ACCESS_FIELDS} == ACCESS
    passage = next(p for p in parsed.retrieve("边界转移显微探针") if p.source["title"] == "边界转移显微探针")
    assert {key: passage.source[key] for key in SOURCE_ACCESS_FIELDS} == ACCESS
    assert passage.evidence["yuzheng_stance_weight"] == "not-evidence"
    assert passage.evidence["evidence_role"] == "speaker-attributed-speech"


def test_model_sees_access_and_asr_limits_without_server_links(index):
    passage = member_passage(index)
    evidence = model_evidence(passage)
    assert evidence["text_access"] == "public" and evidence["source_visibility"] == "members-only"
    assert evidence["transcript_quality"] == "uncorrected-asr"
    assert evidence["speaker_classification"] == "mixed-or-unresolved"
    assert evidence["rights_scope"] == "publisher-authorized-transcript"
    assert evidence["license"] == "LicenseRef-Original-Rights-Retained"
    assert not {"url", "membership_url", "public_copy_url", "source_path", "docindex"}.intersection(evidence)
    assert "YouTube" in SYSTEM_PROMPT and "Founding Member" in SYSTEM_PROMPT


def test_selected_sources_fallback_and_detached_archive_keep_access(index):
    passage = member_passage(index)
    result = assemble_answer(answer_for(passage.source["id"]), [passage])
    fallback = sources_only([passage])
    snapshot = archived_answer(result)
    for value in (result, fallback, snapshot):
        assert {key: value["sources"][0][key] for key in SOURCE_ACCESS_FIELDS} == ACCESS
    passage.source["membership_platform"] = "changed-after-snapshot"
    assert snapshot["sources"][0]["membership_platform"] == "youtube"
    assert "source_path" not in snapshot["sources"][0]


def test_search_sse_candidates_and_final_result_preserve_optional_fields(context_pack, monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", "")
    monkeypatch.setenv("ASK_QUOTA_ENABLED", "false")
    monkeypatch.setenv("ASK_OPS_ENABLED", "false")
    with TestClient(create_app(context_pack)) as client:
        member_passage(client.app.state.index)
        search = client.get("/api/search", params={"q": "职业选择能力作品"})
        assert search.status_code == 200
        member = next(s for s in search.json()["sources"] if s["source_type"] == "video-transcript")
        assert member["source_visibility"] == "members-only" and member["text_access"] == "public"
        response = client.post("/api/ask", json={"question": "职业选择能力作品", "intent": "find"})
        stream = events(response)
        candidate_events = [value for kind, value in stream if kind == "sources"]
        assert candidate_events
        final = next(value for kind, value in stream if kind == "result")
        for value in [*candidate_events, final]:
            selected = next(s for s in value["sources"] if s["source_type"] == "video-transcript")
            assert {key: selected[key] for key in SOURCE_ACCESS_FIELDS} == ACCESS


def test_browser_source_helpers_and_discovery_keep_metadata_without_account_gating():
    # Node imports production JS modules; no DOM, model, login or real network.
    code = r'''
import assert from 'node:assert/strict';
import {isMemberVideo,memberJoinUrl,memberVideoUrl,sourceAccessNote,sourceCopyText,sourceTypeLabel,transcriptQualityNote,YOUTUBE_MEMBERSHIP_URL} from './src/source-access.js';
import {discoveryDetail} from './src/discovery.js';
const source={id:'S1',title:'Synthetic member video',source_type:'video-transcript',date:'2026-10-02',url:'https://www.youtube.com/watch?v=synthetic&t=900s',public_copy_url:'https://example.test/transcript',
 source_visibility:'members-only',text_access:'public',membership_platform:'youtube',membership_url:YOUTUBE_MEMBERSHIP_URL,membership_verified_at:'2026-10-02',transcript_source_kind:'previously-included-transcript',transcript_quality:'uncorrected-asr',speaker_classification:'mixed-or-unresolved'};
assert.equal(sourceTypeLabel(source),'会员视频');
assert.equal(sourceTypeLabel({source_type:'video-transcript',title:'会员视频'}),'视频');
assert.equal(isMemberVideo({source_type:'community-post',source_visibility:'members-only'}),false);
assert.equal(memberJoinUrl([source, {...source,id:'S2'}]),YOUTUBE_MEMBERSHIP_URL);
assert.equal(memberJoinUrl([{...source,source_visibility:'public'}]),null);
assert.equal(memberJoinUrl([{...source,membership_url:'https://attacker.test/join'}]),null);
assert.equal(memberJoinUrl([{...source,membership_platform:'superlinear'}]),null);
assert.equal(memberJoinUrl([{...source,founding:true,source_visibility:undefined}]),null);
assert.equal(memberVideoUrl(source),'https://www.youtube.com/watch?v=synthetic');
for(const url of ['javascript:alert(1)','http://www.youtube.com/watch?v=synthetic','https://youtube.com.attacker.test/watch?v=synthetic','https://user:secret@www.youtube.com/watch?v=synthetic']) assert.equal(memberVideoUrl({...source,url}),null);
assert.match(sourceAccessNote(source),/文字稿已公开/);
assert.match(sourceAccessNote(source),/YouTube/);
assert.match(sourceCopyText(source),/会员视频/);
assert.match(sourceCopyText(source),/公开文字稿：https:\/\/example.test\/transcript/);
assert.equal(transcriptQualityNote(source),'自动识别稿，尚未校对。');
assert.equal(transcriptQualityNote({...source,transcript_quality:'human-caption'}),'');
globalThis.fetch=async(_url,options)=>{
 assert.equal(options.credentials,'omit');
 return Response.json({question:'Synthetic public question',answer:{summary:'Synthetic summary',sections:[],sources:[{...source,identity:'must not pass',reasoning_content:'must not pass'}]}});
};
const detail=await discoveryDetail('01234567-89ab-4cde-8f01-23456789abcd');
for(const key of ['source_visibility','text_access','membership_platform','membership_url','membership_verified_at','transcript_source_kind','transcript_quality','speaker_classification']) assert.equal(detail.answer.sources[0][key],source[key]);
assert.equal(detail.answer.sources[0].identity,undefined);
assert.equal(detail.answer.sources[0].reasoning_content,undefined);
console.log('synthetic source contract passed');
'''
    result = subprocess.run(["node", "--input-type=module", "-e", code], cwd=PROJECT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "synthetic source contract passed"
