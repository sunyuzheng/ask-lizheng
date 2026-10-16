"""External audio keeps its own source URL, attribution and dialogue counts."""
from types import SimpleNamespace

import pytest

from server.retrieval import ContextIndex, TIMESTAMP, timed_url
from server.semantic import SemanticIndexError, prepare_index
from test_backend import context_pack, source_file
from test_dialogue_layers import dialogue
from test_semantic import write_manifest


@pytest.mark.parametrize("license,scope", [
    ("LicenseRef-Lizheng-Reference-Use-1.0", "publisher-authorized-transcript"),
    ("LicenseRef-Original-Rights-Retained", "third-party-reference"),
])
def test_audio_statements_are_answer_sources_and_count_as_dialogues(context_pack, license, scope):
    before = ContextIndex(context_pack); before.load()
    count = before.metadata()["counts"]["yuzheng_dialogue_sources"]
    url = "https://podcast.example.test/episode/published-audio"
    source_file(context_pack, "corpus/videos", "external-audio.md", {
        "id": "dialogue-external-audio", "title": "合成音频归属探针", "source_type": "audio-transcript",
        "source_url": url, "source_family": url, "published_at": "2026-09-22",
        "data_layer": "raw", "content_origin": "yuzheng-spoken-source", "evidence_role": "primary-speech",
        "yuzheng_stance_weight": "direct-with-quotation-boundaries", "speaker_id": "yuzheng",
        "speaker_name": "立正", "speaker_status": "reviewed-source-attribution", "speech_act": "statement",
        "speaker_classification": "yuzheng-turns-reviewed", "transcript_quality": "ai-text-reviewed",
        "license": license, "rights_scope": scope,
    }, "[00:01:01] 合成音频归属探针，已确认说话人的陈述。" + "用于验证短引用边界的合成文字。" * 100)
    index = ContextIndex(context_pack); index.load()
    assert index.metadata()["counts"]["yuzheng_dialogue_sources"] == count + 1
    passages = index.retrieve("合成音频归属探针", intent="understand")
    selected = next(p for p in passages if p.source["source_type"] == "audio-transcript")
    assert selected.source["url"] == url
    assert selected.source["timecode"] == "00:01:01"
    assert selected.source["speaker_id"] == "yuzheng"
    assert len(selected.source["excerpt"]) <= 500


def test_display_only_time_never_manufactures_a_seek_link():
    anchor = TIMESTAMP.search("[00:01:01] Synthetic speech")
    youtube = "https://www.youtube.com/watch?v=ABCDEFGHIJK"
    assert timed_url(SimpleNamespace(source_url=youtube), anchor) == youtube + "&t=61s"
    assert timed_url(SimpleNamespace(source_url=youtube, timestamp_mode="display-only"), anchor) == ""
    assert timed_url(SimpleNamespace(source_url="https://example.test/audio.mp3"), anchor) == ""
    linked = TIMESTAMP.search(f"[00:01:01]({youtube}&t=61s) Synthetic speech")
    assert timed_url(SimpleNamespace(source_url=youtube, timestamp_mode="display-only"), linked) == ""


def test_semantic_projection_binds_timestamp_mode(context_pack):
    dialogue(context_pack)
    index = ContextIndex(context_pack); index.load()
    documents = [doc for doc in index.documents if doc.path.startswith("corpus/dialogues/")]
    write_manifest(context_pack)
    prepare_index(context_pack, documents)
    documents[0].timestamp_mode = "display-only"
    with pytest.raises(SemanticIndexError, match="attribution differs"):
        prepare_index(context_pack, documents)
