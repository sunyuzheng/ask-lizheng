"""Synthetic end-to-end source layers; no real speech or model calls."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.answers import assemble_answer, model_evidence, misattributed
from server.curate import CurateSource
from server.ops_records import archived_answer
from server.retrieval import ContextIndex
from server.semantic import SemanticIndexError, prepare_index
from server.context_architecture import CardValidationError, load as load_architecture
from test_context_architecture import make_pack
from test_backend import answer_for, context_pack, source_file
from test_semantic import write_manifest


def dialogue(root):
    path = root / "corpus/dialogues/synthetic.json"
    path.parent.mkdir(parents=True)
    data = {
        "schema_version": 1, "data_layer": "raw", "id": "youtube-synthetic",
        "title": "合成归属探针", "video_id": "synthetic", "source_type": "video-transcript",
        "source_url": "https://www.youtube.com/watch?v=synthetic", "source_family": "synthetic-conversation",
        "content_origin": "mixed-or-unresolved-speech", "evidence_role": "speaker-attributed-speech",
        "yuzheng_stance_weight": "not-evidence", "speaker_classification": "mixed-or-unresolved",
        "units": [
            {"id": "cue-1", "start_seconds": 1, "end_seconds": 3, "text": "归属探针，立正提出的问题。",
             "speaker_id": "yuzheng", "speaker_name": "立正", "speaker_status": "reviewed-source-attribution"},
            {"id": "cue-2", "start_seconds": 4, "end_seconds": 6, "text": "归属探针，合成嘉宾的回答。",
             "speaker_id": "guest-synthetic", "speaker_name": "合成嘉宾", "speaker_status": "reviewed-source-attribution"},
            {"id": "cue-3", "start_seconds": 7, "end_seconds": 9, "text": "归属探针，这一段尚未确认说话人。",
             "speaker_id": "unknown", "speaker_name": "", "speaker_status": "unresolved"},
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def test_both_speakers_and_unknown_survive_retrieval_without_merging(context_pack):
    dialogue(context_pack)
    index = ContextIndex(context_pack)
    index.load()
    passages = index.retrieve("归属探针", limit=8)
    owners = {p.source["speaker_id"]: p for p in passages if p.evidence["source_family"] == "synthetic-conversation"}
    assert set(owners) == {"yuzheng", "guest-synthetic", "unknown"}
    assert "嘉宾的回答" not in owners["yuzheng"].source["excerpt"]
    assert "立正提出" not in owners["guest-synthetic"].source["excerpt"]
    assert owners["unknown"].evidence["yuzheng_stance_weight"] == "not-evidence"
    assert owners["unknown"].source["speaker_status"] == "unresolved"
    for owner, passage in owners.items():
        evidence = model_evidence(passage)
        assert evidence["data_layer"] == "raw" and evidence["speaker_id"] == owner
        answer = assemble_answer(answer_for(passage.source["id"]), [passage])
        archived = archived_answer(answer)
        assert archived["sources"][0]["speaker_id"] == owner
        assert archived["sources"][0]["fragment_id"] == passage.source["fragment_id"]
        assert CurateSource.model_validate(passage.source).speaker_id == owner


def test_pinned_structured_projection_rejects_text_and_owner_spoof(context_pack):
    dialogue(context_pack)
    index = ContextIndex(context_pack)
    index.load()
    docs = [d for d in index.documents if d.path.startswith("corpus/dialogues/")]
    write_manifest(context_pack)
    metadata, inputs = prepare_index(context_pack, docs)
    assert len(metadata["entries"]) == 3 and len(inputs) == 3
    unknown = next(d for d in docs if d.speaker_id == "unknown")
    unknown.speaker_id = "yuzheng"
    with pytest.raises(SemanticIndexError, match="attribution differs"):
        prepare_index(context_pack, docs)
    unknown.speaker_id = "unknown"
    unknown.text += "捏造的逐字稿句子。"
    with pytest.raises(SemanticIndexError, match="attribution differs"):
        prepare_index(context_pack, docs)


def test_participant_name_discovery_does_not_assign_unknown_turns(context_pack):
    path = dialogue(context_pack)
    data = json.loads(path.read_text())
    data["participant_names"] = "合成目录嘉宾名"
    data["transcript_quality"] = "ai-text-reviewed"
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack)
    index.load()
    passages = index.retrieve("合成目录嘉宾名", limit=8)
    unknown = next(p for p in passages if p.source.get("speaker_id") == "unknown")
    assert unknown.source["author"] == "说话人待核"
    assert unknown.source["participant_names"] == "合成目录嘉宾名"
    assert model_evidence(unknown)["transcript_quality"] == "ai-text-reviewed"


def test_alias_and_stable_identity_survive_answer_pipeline_without_attribution(context_pack):
    path = dialogue(context_pack)
    data = json.loads(path.read_text())
    data.update(participant_entity_ids=["guest:synthetic"], participant_aliases=["合成身份别名"],
                participant_metadata_basis="programme association only")
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack); index.load()
    passages = index.retrieve("合成身份别名", limit=8)
    unknown = next(p for p in passages if p.source.get("speaker_id") == "unknown")
    assert unknown.source["participant_entity_ids"] == "guest:synthetic"
    assert model_evidence(unknown)["participant_aliases"] == "合成身份别名"
    assert unknown.evidence["yuzheng_stance_weight"] == "not-evidence"
    result = assemble_answer(answer_for(unknown.source["id"]), [unknown])
    assert archived_answer(result)["sources"][0]["participant_metadata_basis"] == "programme association only"
    assert CurateSource.model_validate(unknown.source).speaker_name == ""


def test_unresolved_dialogue_returns_separate_windows_not_separate_people(context_pack):
    path = dialogue(context_pack); data = json.loads(path.read_text())
    data["units"] = [{"id": f"cue-{i}", "start_seconds": i * 30, "end_seconds": i * 30 + 20,
                      "text": f"合成分片探针{i} " + "x" * 2590, "speaker_id": "unknown", "speaker_name": "", "speaker_status": "unresolved"}
                     for i in range(1, 6)]
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack); index.load()
    passages = [p for p in index.retrieve("合成分片探针", limit=8) if p.evidence["source_family"] == "synthetic-conversation"]
    assert len(passages) == 3
    assert len({p.source["fragment_id"] for p in passages}) == 3
    assert all(p.source["speaker_id"] == "unknown" and p.evidence["yuzheng_stance_weight"] == "not-evidence" for p in passages)


def test_reasoning_card_uses_raw_part_and_never_annotation_in_same_file(tmp_path):
    _, docs = make_pack(tmp_path)
    from types import SimpleNamespace
    annotation = SimpleNamespace(**{**vars(docs[1]), "id": "original#annotation", "data_layer": "derived"})
    architecture = load_architecture(tmp_path, [*docs, annotation])
    assert architecture.linked_candidates(["learning-transfer"])[0]["docindex"] == 1
    docs[1].text = "原话没有命中概念。"
    with pytest.raises(CardValidationError, match="focus has no matching primary"):
        load_architecture(tmp_path, [*docs, annotation])


def test_raw_quote_embedding_excludes_ai_heading_and_background(context_pack):
    source_file(context_pack, "corpus/conversation-excerpts", "synthetic-quote.md", {
        "id": "synthetic-quote", "title": "合成原话", "source_type": "video-excerpt",
        "source_url": "https://www.youtube.com/watch?v=synthetic",
        "speaker_classification": "yuzheng-turns-reviewed", "content_origin": "yuzheng-spoken-source",
        "evidence_role": "primary-speech", "yuzheng_stance_weight": "direct-with-quotation-boundaries",
    }, "## AI生成的观点标题\n\n[00:00:01](https://www.youtube.com/watch?v=synthetic&t=1s)\n\n> 这是合成原话。\n> 原话第二句。\n\n背景：AI生成的解读。")
    index = ContextIndex(context_pack)
    index.load()
    docs = [d for d in index.documents if d.source_id == "synthetic-quote"]
    assert {d.data_layer for d in docs} == {"raw", "derived"}
    write_manifest(context_pack)
    _, inputs = prepare_index(context_pack, docs)
    assert len(inputs) == 1 and "原话第二句" in inputs[0]
    assert "AI生成" not in inputs[0] and "背景" not in inputs[0]


def test_source_labels_and_history_keep_fragment_attribution():
    project = Path(__file__).resolve().parents[1]
    code = """
import assert from 'node:assert/strict';
import {sourceAttributionLabel} from './src/source-attribution.js';
import {sourceCopyText, transcriptQualityNote} from './src/source-access.js';
assert.equal(sourceAttributionLabel({data_layer:'raw',speaker_id:'unknown'}), '说话人待核');
assert.equal(sourceAttributionLabel({data_layer:'raw',speaker_id:'guest',speaker_name:'合成嘉宾'}), '发言：合成嘉宾');
assert.equal(sourceAttributionLabel({data_layer:'derived',author:'AI',speaker_id:'yuzheng',speaker_name:'立正'}), 'AI整理');
assert.equal(sourceAttributionLabel({data_layer:'derived',content_origin:'published-editorial-adaptation'}), '整理稿');
assert.equal(sourceAttributionLabel({source_type:'community-post'}), '');
const source={data_layer:'raw',speaker_id:'unknown',title:'合成资料',transcript_quality:'ai-text-reviewed'};
assert.match(transcriptQualityNote(source), /未逐句听校/);
assert.match(sourceCopyText(source), /说话人待核/);
assert.match(sourceCopyText(source), /未逐句听校/);
"""
    subprocess.run(["node", "--input-type=module", "-e", code], cwd=project, check=True, capture_output=True, text=True)


def test_published_summary_has_separate_provenance_in_evidence_archive_and_semantic(context_pack):
    import hashlib
    source_file(context_pack, "corpus/community-posts", "synthetic-summary.md", {
        "id": "synthetic-summary", "title": "合成总结分层探针", "source_type": "community-post",
        "author": "Yuzheng Sun", "publisher": "Yuzheng Sun", "original_author": "Yuzheng Sun",
        "source_url": "https://example.org/synthetic-summary", "source_family": "summary-family",
        "content_origin": "yuzheng-published-text", "evidence_role": "published-source",
        "yuzheng_stance_weight": "direct-with-quotation-boundaries",
    }, "原始前言合成探针。\n\n## 以下是合成AI总结\n\n整理正文合成探针，不能算作任何人的原话。")
    source = context_pack / "corpus/community-posts/synthetic-summary.md"
    policy = context_pack / "config/source-section-layers.json"
    policy.parent.mkdir(exist_ok=True)
    policy.write_text(json.dumps({"schema_version": 1, "records": [{
        "path": source.relative_to(context_pack).as_posix(), "source_id": "synthetic-summary",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "layer": "derived",
        "start_marker": "## 以下是合成AI总结", "writer": "AI", "content_origin": "ai-synthesis",
        "generation_method": "source-declared-ai-written", "classification_basis": "合成发布者明确声明。",
    }]}, ensure_ascii=False))
    index = ContextIndex(context_pack)
    index.load()
    docs = [d for d in index.documents if d.source_id == "synthetic-summary"]
    assert {d.data_layer for d in docs} == {"raw", "derived"}
    write_manifest(context_pack)
    _, inputs = prepare_index(context_pack, docs)
    assert len(inputs) == 1 and "原始前言" in inputs[0] and "整理正文" not in inputs[0]
    passage = next(p for p in index.retrieve("整理正文合成探针") if p.source.get("data_layer") == "derived")
    assert passage.source["author"] == "AI" and passage.source["speaker_status"] == "not-speech"
    assert passage.source["content_origin"] == "ai-synthesis"
    assert passage.source["generation_method"] == "source-declared-ai-written"
    assert model_evidence(passage)["yuzheng_stance_weight"] == "secondary-only"
    archived = archived_answer(assemble_answer(answer_for(passage.source["id"]), [passage]))
    assert archived["sources"][0]["content_origin"] == "ai-synthesis"
    assert CurateSource.model_validate(passage.source).generation_method == "source-declared-ai-written"


def test_reference_use_excerpt_stays_short_and_finds_the_matching_cue(context_pack):
    path = dialogue(context_pack)
    data = json.loads(path.read_text())
    data["license"] = "LicenseRef-Lizheng-Reference-Use-1.0"
    data["units"] = [{"id": f"cue-{i}", "start_seconds": i, "end_seconds": i + 1,
                      "text": "合成普通背景文字。" * 15, "speaker_id": "unknown", "speaker_status": "unresolved"}
                     for i in range(30)]
    data["units"][15]["text"] = "独特匹配探针。" * 100
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack)
    index.load()
    passage = next(p for p in index.retrieve("独特匹配探针") if p.source.get("speaker_id") == "unknown")
    assert len(passage.source["excerpt"]) <= 500
    assert "独特匹配探针" in passage.source["excerpt"] and passage.source["timecode"] == "00:00:15"


def test_host_question_and_reported_attitude_are_preserved_without_stance_upgrade(context_pack):
    path = dialogue(context_pack); data = json.loads(path.read_text())
    data["units"][0]["speech_act"] = "question"
    data["units"][1]["speech_act"] = "reported-other"
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack); index.load()
    passages = index.retrieve("归属探针", limit=8)
    for owner, act in [("yuzheng", "question"), ("guest-synthetic", "reported-other")]:
        passage = next(p for p in passages if p.source.get("speaker_id") == owner)
        assert passage.evidence["yuzheng_stance_weight"] == "not-evidence"
        assert model_evidence(passage)["speech_act"] == act
        assert CurateSource.model_validate(passage.source).speech_act == act
        answer = assemble_answer(answer_for(passage.source["id"]), [passage])
        assert archived_answer(answer)["sources"][0]["speech_act"] == act


@pytest.mark.parametrize("act", ["question", "introduction", "acknowledgement", "joke", "reported-other"])
def test_host_non_stance_turn_cannot_support_a_claim_of_his_view(context_pack, act):
    path = dialogue(context_pack); data = json.loads(path.read_text())
    data["units"][0]["speech_act"] = act
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack); index.load()
    passage = next(p for p in index.retrieve("归属探针", limit=8) if p.source.get("speaker_id") == "yuzheng")
    source_id = passage.source["id"]
    assert misattributed(answer_for(source_id, body=f"立正认为合成前提成立。[{source_id}]"), [passage])
    assert not misattributed(answer_for(source_id, body=f"立正提出了一个问题。[{source_id}]"), [passage])
    assert not misattributed(answer_for(source_id, body=f"不能据此认定立正认为合成前提成立。[{source_id}]"), [passage])


def test_confirmed_host_statement_can_support_a_claim_of_his_view(context_pack):
    path = dialogue(context_pack); data = json.loads(path.read_text())
    data["units"][0]["speech_act"] = "statement"
    path.write_text(json.dumps(data, ensure_ascii=False))
    index = ContextIndex(context_pack); index.load()
    passage = next(p for p in index.retrieve("归属探针", limit=8) if p.source.get("speaker_id") == "yuzheng")
    source_id = passage.source["id"]
    assert not misattributed(answer_for(source_id, body=f"立正认为合成前提成立。[{source_id}]"), [passage])


def test_substantive_guest_answer_is_not_displaced_by_generic_introduction(context_pack):
    path = dialogue(context_pack); data = json.loads(path.read_text())
    data.update(participant_names="Synthetic guest", participant_aliases=["synthetic-alias"],
                participant_entity_ids=["guest-synthetic"], participant_metadata_basis="Synthetic programme only")
    data["units"] = [
        {"id": "cue-1", "start_seconds": 1, "end_seconds": 3, "text": "synthetic-alias synthetic-alias background",
         "speaker_id": "guest-synthetic", "speaker_name": "Synthetic guest", "speaker_status": "reviewed-source-attribution", "speech_act": "introduction"},
        {"id": "cue-2", "start_seconds": 4, "end_seconds": 6, "text": "skepticism remains; evidence first",
         "speaker_id": "guest-synthetic", "speaker_name": "Synthetic guest", "speaker_status": "reviewed-source-attribution", "speech_act": "statement"},
        {"id": "cue-3", "start_seconds": 7, "end_seconds": 9, "text": "What is your skepticism?",
         "speaker_id": "yuzheng", "speaker_name": "立正", "speaker_status": "reviewed-source-attribution", "speech_act": "question"}]
    path.write_text(json.dumps(data, ensure_ascii=False)); index = ContextIndex(context_pack); index.load()
    passages = index.retrieve("synthetic-alias skepticism", limit=8)
    guest = next(p for p in passages if p.source.get("speaker_id") == "guest-synthetic")
    assert "evidence first" in guest.source["excerpt"]
    assert guest.source["speech_act"] == "statement"
    intro = next(p for p in index.retrieve("synthetic-alias background", limit=8) if p.source.get("speaker_id") == "guest-synthetic")
    assert intro.source["speech_act"] == "introduction"


def test_public_programme_context_disambiguates_same_first_name_without_assigning_turns(context_pack):
    first = dialogue(context_pack); data = json.loads(first.read_text())
    for unit in data["units"]: unit.update(speaker_id="unknown", speaker_name="", speaker_status="unresolved")
    data.update(participant_names="Synthetic common name", participant_aliases=["common-name"], source_context="Synthetic project-alpha programme")
    first.write_text(json.dumps(data, ensure_ascii=False))
    other = json.loads(json.dumps(data)); other.update(id="youtube-other", source_family="other-conversation", source_context="Unrelated project-beta")
    for unit in other["units"]: unit["text"] = "common-name common-name common-name"
    (first.parent / "other.json").write_text(json.dumps(other, ensure_ascii=False))
    index = ContextIndex(context_pack); index.load()
    passages = index.retrieve("common-name project-alpha", limit=8)
    assert passages[0].evidence["source_family"] == "synthetic-conversation"
    assert passages[0].source["speaker_id"] == "unknown"
    assert passages[0].evidence["yuzheng_stance_weight"] == "not-evidence"


def test_shared_catalog_retrieval_and_embedding_keep_turn_ownership(context_pack):
    path = dialogue(context_pack)
    catalog = context_pack / "catalog/guests.json"
    catalog.write_text(json.dumps({
        "entities": [{"entity_id": "guest:synthetic", "display_name": "目录嘉宾", "aliases": ["目录别名"]}],
        "sources": [{"source_id": "youtube-synthetic", "title": "合成节目", "url": "https://www.youtube.com/watch?v=synthetic"}],
        "appearances": [{"entity_id": "guest:synthetic", "source_id": "youtube-synthetic"}],
    }, ensure_ascii=False))
    write_manifest(context_pack)
    index = ContextIndex(context_pack); index.load()
    docs = [d for d in index.documents if d.path == path.relative_to(context_pack).as_posix()]
    _, inputs = prepare_index(context_pack, docs)
    assert all(d.participant_aliases == "目录别名" for d in docs)
    assert all("目录别名" not in text for text in inputs)
    unknown = next(d for d in docs if d.speaker_id == "unknown")
    assert unknown.speaker_name == "" and unknown.yuzheng_stance_weight == "not-evidence"
    passages = index.retrieve("目录别名")
    assert any(p.source.get("participant_aliases") == "目录别名" for p in passages)
    docs[0].participant_aliases = "spoofed alias"
    with pytest.raises(SemanticIndexError, match="attribution differs"):
        prepare_index(context_pack, docs)


def test_shared_catalog_is_manifest_pinned_before_embedding(context_pack):
    dialogue(context_pack)
    catalog = context_pack / "catalog/guests.json"
    catalog.write_text(json.dumps({"entities": [], "sources": [], "appearances": []}))
    write_manifest(context_pack)
    index = ContextIndex(context_pack); index.load()
    catalog.write_text(json.dumps({"entities": [], "sources": [], "appearances": [], "drift": True}))
    with pytest.raises(SemanticIndexError):
        prepare_index(context_pack, index.documents)


def test_explicit_catalog_guest_precedes_unrelated_topic_and_semantic_hits(context_pack):
    path = dialogue(context_pack)
    source_file(context_pack, "corpus/community-posts", "topic.md", {
        "id": "unrelated-topic", "title": "职业发展", "source_type": "community-post",
        "author": "Yuzheng Sun", "source_url": "https://example.org/topic",
        "content_origin": "yuzheng-published-text", "evidence_role": "published-source",
    }, "职业发展与学习方法。" * 30)
    (context_pack / "catalog/guests.json").write_text(json.dumps({
        "entities": [{"entity_id": "guest:synthetic", "display_name": "合成嘉宾", "aliases": ["Synthetic Person"]}],
        "sources": [{"source_id": "youtube-synthetic", "url": "https://www.youtube.com/watch?v=synthetic"}],
        "appearances": [{"entity_id": "guest:synthetic", "source_id": "youtube-synthetic"}],
    }))
    index = ContextIndex(context_pack); index.load()
    unrelated = next(i for i, doc in enumerate(index.documents) if doc.source_id == "unrelated-topic")
    passages = index.retrieve("请问合成嘉宾如何看职业发展", semantic_candidates=[{"docindex": unrelated, "score": .99}])
    assert passages[0].source["participant_entity_ids"] == "guest:synthetic"
    assert any(p.source.get("speaker_id") == "unknown" and p.source["speaker_status"] == "unresolved" for p in passages)
    assert index.retrieve("职业发展")[0].evidence["source_family"] != "synthetic-conversation"
    assert index.participant_entities("Synthetic Person 职业发展") == {"guest:synthetic"}
    assert index.participant_entities("XSynthetic Person 职业发展") == set()


def test_catalog_name_resolution_preserves_shared_aliases_and_specific_names():
    index = ContextIndex(Path("unused"))
    index.participant_aliases = {"jo": {"guest:a", "guest:b"}, "synthetic jo": {"guest:a"}, "john": {"guest:c"}}
    assert index.participant_entities("Jo") == {"guest:a", "guest:b"}
    assert index.participant_entities("Synthetic Jo") == {"guest:a"}
    assert index.participant_entities("John") == {"guest:c"}
    assert index.participant_entities("enjoy") == set()
