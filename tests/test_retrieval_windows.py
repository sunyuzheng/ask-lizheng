"""Evidence window behavior over independent synthetic sources, without providers."""
import json
from collections import Counter

import pytest

from server.answers import model_evidence
from server.retrieval import ContextIndex, is_yuzheng_basis
from server.dialogue_evidence import supports_speaker
from server.semantic import embedding_inputs
from test_backend import context_pack, source_file
from test_dialogue_layers import dialogue


def window_index(root):
    first = dialogue(root)
    data = json.loads(first.read_text())
    for label in ("alpha", "beta"):
        data.update(id=f"youtube-synthetic-{label}", title=f"窗口合成探针 {label}",
                    source_family=f"window-family-{label}",
                    source_url=f"https://www.youtube.com/watch?v=synthetic-{label}")
        data["units"] = [{"id": f"{data['id']}#cue-{i}", "start_seconds": i * 30, "end_seconds": i * 30 + 20,
                          "text": f"窗口合成探针{i} " + "x" * 2580, "speaker_id": "yuzheng", "speaker_name": "立正",
                          "speaker_status": "reviewed-source-attribution", "speech_act": "statement"}
                         for i in range(1, 6)]
        (first.parent / f"windows-{label}.json").write_text(json.dumps(data, ensure_ascii=False))
    first.unlink()
    index = ContextIndex(root)
    index.load()
    return index


def test_same_speaker_nonoverlapping_windows_survive_across_independent_families(context_pack):
    index = window_index(context_pack)
    passages = index.retrieve("窗口合成探针", limit=12, intent="understand")
    assert Counter(p.evidence["source_family"] for p in passages) == {
        "window-family-alpha": 3, "window-family-beta": 3}
    assert len({p.source["fragment_id"] for p in passages}) == 6
    assert all(supports_speaker(p.evidence, "yuzheng") for p in passages)
    assert all(p.source["speaker_id"] == "unknown" for p in passages)  # mixed-source envelope
    assert [p.evidence["document_id"] for p in passages] == [
        p.evidence["document_id"] for p in index.retrieve("窗口合成探针", limit=12, intent="understand")]


def test_family_budget_also_counts_an_initial_derived_representation(context_pack):
    source_file(context_pack, "context", "derived-window.md", {
        "id": "synthetic-derived-window", "title": "Synthetic navigation", "source_type": "context",
        "source_url": "https://example.org/navigation", "source_family": "window-family-alpha",
        "data_layer": "derived", "content_origin": "ai-synthesis", "generation_method": "ai-written",
        "evidence_role": "secondary-synthesis", "yuzheng_stance_weight": "not-evidence",
    }, "Synthetic navigation body, without a speaker's statement.")
    index = window_index(context_pack)
    documents = list(index.documents)
    first = next(i for i, doc in enumerate(documents) if doc.source_id == "synthetic-derived-window")
    rows = [first] + [i for i, doc in enumerate(documents)
                      if doc.source_family.startswith("window-family-") and doc.speaker_id == "yuzheng"]
    passages = index.retrieve("毫不相关的搜索语句", limit=12, intent="find",
                              semantic_candidates=[{"docindex": i, "score": .9} for i in rows])
    assert passages[0].evidence["docindex"] == first
    assert Counter(p.evidence["source_family"] for p in passages) == {
        "window-family-alpha": 3, "window-family-beta": 3}


@pytest.mark.parametrize("mutation", ["overlap", "cross-snapshot", "missing-snapshot", "unparseable"])
def test_repeat_windows_fail_closed_without_proven_independence(context_pack, mutation):
    index = window_index(context_pack)
    documents = list(index.documents)
    for i, doc in enumerate(documents):
        if doc.source_family.startswith("window-family-"):
            if mutation == "overlap":
                doc.fragment_id = f"{doc.source_id}#cue-1-{doc.source_id}#cue-9"
            elif mutation == "cross-snapshot":
                doc.source_snapshot_sha256 = f"{i:064x}"
            elif mutation == "missing-snapshot":
                doc.source_snapshot_sha256 = ""
            else:
                doc.fragment_id = f"{doc.source_id}#unparseable-{i}"
    index.documents = documents
    passages = index.retrieve("窗口合成探针", limit=12, intent="understand")
    assert Counter(p.evidence["source_family"] for p in passages) == {
        "window-family-alpha": 1, "window-family-beta": 1}


def test_semantic_only_windows_reach_the_model_without_overriding_dual_channel_excerpts(context_pack):
    url = "https://www.youtube.com/watch?v=synthetic-window"
    body = (f"[00:01:00]({url}&t=60s) 方法开场。" + "普通背景。" * 480
            + f"[00:12:34]({url}&t=754s) 独立的结束线索clue。" + "尾部。" * 20)
    source_file(context_pack, "corpus/videos", "semantic-window.md", {
        "id": "synthetic-window", "title": "合成分段原文", "source_type": "video-transcript",
        "source_url": url, "source_family": "synthetic-window-family", "author": "Yuzheng Sun",
        "content_origin": "yuzheng-spoken-source", "evidence_role": "primary-speech",
        "yuzheng_stance_weight": "direct", "speaker_id": "yuzheng",
        "speaker_status": "reviewed-source-attribution", "license": "LicenseRef-Lizheng-Reference-Use-1.0",
    }, body)
    index = ContextIndex(context_pack)
    index.load()
    docindex = next(i for i, d in enumerate(index.documents) if d.source_id == "synthetic-window")
    doc = index.documents[docindex]
    segment = len(embedding_inputs(doc)) - 1
    assert segment > 0
    candidate = {"docindex": docindex, "score": .9}

    def selected(row, question="unseen paraphrase"):
        return next(p for p in index.retrieve(question, intent="understand", semantic_candidates=[row])
                    if p.evidence["docindex"] == docindex)

    legacy = selected(candidate)
    assert "方法开场" in legacy.source["excerpt"] and "独立的结束线索" not in legacy.source["excerpt"]
    passage = selected({**candidate, "segment": segment})
    assert "独立的结束线索" in model_evidence(passage)["excerpt"]
    assert "方法开场" not in passage.source["excerpt"]
    assert len(passage.source["excerpt"]) <= 500
    direct = selected(candidate, "方法如何改变")
    dual = selected({**candidate, "segment": segment}, "方法如何改变")
    assert dual.source["excerpt"] == direct.source["excerpt"]
    assert "方法开场" in dual.source["excerpt"] and "独立的结束线索" not in dual.source["excerpt"]
    # One shared body word is insufficient for lexical admission on this
    # multi-term query, while the semantic window still preserves its cue.
    question = "clue hypothetical alternate route"
    assert docindex not in {p.evidence["docindex"] for p in index.retrieve(question, intent="understand")}
    timed = selected({**candidate, "segment": segment}, question)
    assert "独立的结束线索" in timed.source["excerpt"] and timed.source["timecode"] == "00:12:34"
    assert timed.source["url"].endswith("&t=754s")
    excerpt, timecode, target = index._excerpt(doc, ["独立的结束线索"], {"独立的结束线索": 8},
                                               semantic_segment=segment)
    assert "独立的结束线索" in excerpt and timecode == "00:12:34" and target.endswith("&t=754s")
    for invalid in (-1, True, segment + 100):
        assert selected({**candidate, "segment": invalid}).source["excerpt"] == legacy.source["excerpt"]
