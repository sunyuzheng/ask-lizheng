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


def test_release_questions_use_new_gold_files_and_keep_quotation_budgets():
    root = Path(__file__).resolve().parents[1]
    original = bench.load_fixture(root / "tests/fixtures/evidence-benchmark.json")
    release = bench.load_fixture(root / "tests/fixtures/evidence-release-holdout.json")
    assert len(release["cases"]) == 16 and len({c["group"] for c in release["cases"]}) == 8
    assert {r["path"] for r in original["refs"].values()}.isdisjoint(r["path"] for r in release["refs"].values())
    assert all(len(r.get("context", "")) + sum(map(len, r["quotes"])) <= 450
               for f in (original, release) for r in f["refs"].values())


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


def test_public_vector_seed_is_an_exact_projection_of_the_two_public_fixtures():
    root = Path(__file__).resolve().parents[1]
    seed = json.loads(bench.PUBLIC_VECTOR_SEED.read_text())
    assert set(seed) == {"schema_version", "model", "dimensions", "queries"}
    assert seed["schema_version"] == 1 and seed["model"] == semantic.EMBEDDING_MODEL
    assert seed["dimensions"] == semantic.DIMENSIONS
    cases = [c for filename in ("evidence-benchmark.json", "evidence-release-holdout.json")
             for c in bench.load_fixture(root / "tests/fixtures" / filename)["cases"]]
    queries = {bench.digest(bench.semantic_query(c, semantic).encode()): bench.semantic_query(c, semantic) for c in cases}
    assert len(cases) == len(queries) == len(seed["queries"]) == 76
    assert set(queries) == set(seed["queries"])
    for key, query in queries.items():
        row = seed["queries"][key]
        assert set(row) == {"query", "vector"} and row["query"] == query
        assert len(bench.decode(row["vector"], seed["dimensions"])) == semantic.DIMENSIONS


def test_missing_default_cache_reads_seed_without_writing_and_custom_missing_still_fails(tmp_path, monkeypatch):
    cache, seed_path = tmp_path / ".local/default.json", tmp_path / "seed.json"
    monkeypatch.setattr(bench, "DEFAULT_CACHE", cache)
    monkeypatch.setattr(bench, "PUBLIC_VECTOR_SEED", seed_path)
    row = case()
    query = bench.semantic_query(row, semantic)
    value = array("f", [1.0] + [0.0] * (semantic.DIMENSIONS - 1))
    seed = {"schema_version": 1, "model": semantic.EMBEDDING_MODEL, "dimensions": semantic.DIMENSIONS,
            "queries": {bench.digest(query.encode()): {"query": query, "vector": bench.encode(value)}}}
    seed_path.write_text(json.dumps(seed))
    original = seed_path.read_bytes()
    result, _ = asyncio.run(bench.vectors(SimpleNamespace(semantic=semantic), [row], cache))
    assert result[row["id"]] == value and not cache.exists() and seed_path.read_bytes() == original
    with pytest.raises(ValueError, match="Missing cached"):
        asyncio.run(bench.vectors(SimpleNamespace(semantic=semantic), [row], tmp_path / "custom-missing.json"))


def test_refresh_from_seed_writes_only_private_cache_and_never_adds_seed_queries(tmp_path, monkeypatch):
    cache, seed_path = tmp_path / ".local/default.json", tmp_path / "seed.json"
    monkeypatch.setattr(bench, "DEFAULT_CACHE", cache)
    monkeypatch.setattr(bench, "PUBLIC_VECTOR_SEED", seed_path)
    first, second = case("first"), {**case("second"), "question": "另一个公开合成问题"}
    query = bench.semantic_query(first, semantic)
    value = array("f", [1.0] + [0.0] * (semantic.DIMENSIONS - 1))
    seed_path.write_text(json.dumps({"schema_version": 1, "model": semantic.EMBEDDING_MODEL, "dimensions": semantic.DIMENSIONS,
                                    "queries": {bench.digest(query.encode()): {"query": query, "vector": bench.encode(value)}}}))
    original = seed_path.read_bytes()
    calls = []
    async def embed(client, token, texts, timeout):
        calls.append(texts)
        return [value for _ in texts], 1
    fake = SimpleNamespace(**{name: getattr(semantic, name) for name in
                           ("EMBEDDING_MODEL", "DIMENSIONS", "MAX_INPUT_BYTES", "MAX_BATCH", "_byte_windows")},
                           request_embeddings=embed)
    result, _ = asyncio.run(bench.vectors(SimpleNamespace(semantic=fake), [first, second], cache, True, object(), "test-token"))
    assert set(result) == {"first", "second"} and calls == [[bench.semantic_query(second, semantic)]]
    assert len(json.loads(cache.read_text())["queries"]) == 2
    assert cache.stat().st_mode & 0o777 == 0o600 and seed_path.read_bytes() == original


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


def test_mixed_window_anchor_uses_actual_cue_owner_and_delivered_text():
    from types import SimpleNamespace
    from scripts.evidence_eval import attributed_anchor
    units=[{'speaker_id':'yuzheng','speaker_status':'audio-voice-match','text':'明确的目标原句。'},
           {'speaker_id':'guest:alpha','speaker_status':'reviewed-source-attribution','text':'另一位的原句。'}]
    passage=SimpleNamespace(evidence={'speaker_id':'unknown','excerpt':'明确的目标原句。另一位的原句。','dialogue_units':units})
    assert attributed_anchor(passage,'明确的目标原句。','yuzheng')
    assert not attributed_anchor(passage,'另一位的原句。','yuzheng')
    units[0]['speaker_status']='unresolved'
    assert not attributed_anchor(passage,'明确的目标原句。','yuzheng')
    units[0]['speaker_status']='audio-voice-match';passage.evidence['excerpt']='没有提供目标正文。'
    assert not attributed_anchor(passage,'明确的目标原句。','yuzheng')


def test_anchor_never_joins_a_speakers_distant_turns():
    from types import SimpleNamespace
    from scripts.evidence_eval import attributed_anchor
    passage=SimpleNamespace(evidence={'speaker_id':'unknown','excerpt':'首句尾句','dialogue_units':[
        {'speaker_id':'yuzheng','speaker_status':'audio-voice-match','text':'首句'},
        {'speaker_id':'guest:alpha','speaker_status':'audio-voice-match','text':'插入的另一位'},
        {'speaker_id':'yuzheng','speaker_status':'audio-voice-match','text':'尾句'}]})
    assert not attributed_anchor(passage,'首句尾句','yuzheng')
