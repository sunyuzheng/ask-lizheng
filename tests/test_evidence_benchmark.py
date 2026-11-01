"""Offline benchmark contracts: independent anchors, shared vectors, isolated code."""
import asyncio
import json
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import evidence_eval as bench
from server.retrieval import Passage
from server import semantic


def case(identity="q", group="g", split="dev", refs=None):
    return {"id": identity, "group": group, "split": split, "category": "synthetic", "question": "合成问题",
            "intent": "understand", "expect": "answered", "reference_ids": ["r"] if refs is None else refs, "good": "Use the primary evidence."}


def fixture(root, reference=None, cases=None):
    path = root / "corpus/dialogues/source.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"data_layer": "raw", "units": [
        {"text": "甲的独立证据。", "speaker_id": "a"}, {"text": "乙的独立证据。", "speaker_id": "b"}]}))
    (root / "release-manifest.json").write_text(json.dumps({"files": [
        {"path": "corpus/dialogues/source.json", "sha256": bench.digest(path.read_bytes())}]}))
    value = {"schema_version": 1, "context_commit": "a" * 40,
             "refs": {"r": reference or {"path": "corpus/dialogues/source.json", "quotes": ["乙的独立证据。"], "speaker_id": "b"}},
             "cases": cases or [case()]}
    output = root.parent / "fixture.json"
    output.write_text(json.dumps(value))
    return output


def test_gold_validation_requires_the_declared_primary_source_and_speaker(tmp_path):
    root = tmp_path / "context"
    path = fixture(root)
    assert bench.load_fixture(path, root)["refs"]["r"]["speaker_id"] == "b"
    value = json.loads(path.read_text())
    value["refs"]["r"]["speaker_id"] = "a"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="anchor"):
        bench.load_fixture(path, root)
    value["refs"]["r"]["path"] = "../private.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="pinned public"):
        bench.load_fixture(path, root)


def test_paraphrase_group_cannot_cross_dev_holdout(tmp_path):
    path = fixture(tmp_path / "context", cases=[case("a"), case("b", split="holdout")])
    with pytest.raises(ValueError, match="leaks"):
        bench.load_fixture(path)


def test_implementation_modules_are_isolated_even_in_one_process(tmp_path):
    roots = []
    for marker in ("before", "after"):
        root = tmp_path / marker
        (root / "server").mkdir(parents=True)
        (root / "server/semantic.py").write_text(f"MARKER = {marker!r}\n")
        (root / "server/retrieval.py").write_text("from .semantic import MARKER\n")
        (root / "server/answers.py").write_text("from .retrieval import MARKER\n")
        roots.append(root)
    before, after = map(bench.implementation, roots)
    assert before.answers.MARKER == "before" and after.answers.MARKER == "after"
    assert before.retrieval is not after.retrieval


def test_missing_cache_never_calls_embeddings_and_refreshed_vectors_are_shared(tmp_path):
    calls = []
    async def embed(client, token, texts, timeout):
        calls.append(texts)
        assert token == "private-test-token"
        return [array("f", [1.0] + [0.0] * 255) for _ in texts], 1
    fake = SimpleNamespace(**{name: getattr(semantic, name) for name in
                           ("EMBEDDING_MODEL", "DIMENSIONS", "MAX_INPUT_BYTES", "MAX_BATCH", "_byte_windows")},
                           request_embeddings=embed)
    impl, cache = SimpleNamespace(semantic=fake), tmp_path / "vectors.json"
    cases = [case("a"), {**case("b"), "question": "那具体怎么做", "history": [{"question": "前一轮问题", "summary": ""}]}]
    with pytest.raises(ValueError, match="Missing cached"):
        asyncio.run(bench.vectors(impl, cases, cache))
    assert calls == [] and not cache.exists()
    first, metadata = asyncio.run(bench.vectors(impl, cases, cache, True, object(), "private-test-token"))
    second, same = asyncio.run(bench.vectors(impl, cases, cache))
    assert len(calls) == 1 and metadata == same and first == second
    assert calls[0][1].endswith("前一轮问题")
    assert "private-test-token" not in cache.read_text() and cache.stat().st_mode & 0o777 == 0o600
    value = json.loads(cache.read_text())
    value["model"] = "foreign-model"
    cache.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="another embedding"):
        asyncio.run(bench.vectors(impl, cases, cache))


def test_path_hit_and_hidden_document_text_do_not_count_as_model_evidence():
    passage = Passage({"id": "S1", "excerpt": "无关开场"},
                      {"source_path": "corpus/source.md", "source_family": "one", "docindex": 0,
                       "excerpt": "独立证据在完整文档里，但未提供给模型"}, False)
    impl = SimpleNamespace(answers=SimpleNamespace(model_evidence=lambda p: {"excerpt": p.source["excerpt"]}),
                           retrieval=SimpleNamespace(is_yuzheng_basis=lambda d: True))
    index = SimpleNamespace(participant_entities=lambda q: set(), retrieval_query=lambda q, h: q,
                            names_host=lambda q: False, documents=[None])
    refs = {"r": {"path": "corpus/source.md", "quotes": ["独立证据"]}}
    prepared = {"passages": [passage], "route": "generate", "retrieval_ms": 1}
    row = bench.evidence_metrics(impl, index, case(), refs, prepared)
    assert row["path_hits"] == 1 and row["anchor_hits"] == 0 and row["any_reference_hit"] is False
    passage.source["excerpt"] = "独立证据。"
    assert bench.evidence_metrics(impl, index, case(), refs, prepared)["anchor_hits"] == 1


def test_group_weighting_and_pairing_do_not_count_paraphrases_as_independent():
    common = {"reference_count": 1, "anchor_total": 1, "all_eligible": True, "budget_ok": True,
              "family_budget_ok": True, "families": 1, "retrieval_ms": 1}
    rows = [{**common, "id": str(i), "group": group, "any_reference_hit": bool(hit), "all_references_hit": bool(hit), "anchor_hits": hit, "path_hits": 1}
            for i, (group, hit) in enumerate((("a", 1), ("a", 0), ("b", 1)))]
    result = bench.summary(rows)
    assert result["case_reference_hit_rate"] == 2 / 3
    assert result["group_reference_hit_rate"] == .75
    baseline = {"fixture_sha256": "f", "context_commit": "c", "query_vector_sha256": "v", "cases": rows}
    candidate = {**baseline, "query_vector_sha256": "different"}
    with pytest.raises(ValueError, match="query_vector"):
        bench.paired_changes(baseline, candidate)


@pytest.mark.parametrize("value", [array("f", [float("nan")]), array("f", [0.0])])
def test_corrupt_query_vectors_are_rejected(value):
    with pytest.raises(ValueError):
        bench.decode(bench.encode(value), 1)
