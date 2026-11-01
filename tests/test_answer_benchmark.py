"""Benchmark sampling and blinded judging use fake providers only."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts import answer_benchmark as bench
from scripts import evidence_eval as evidence
from test_evidence_benchmark import case, fixture

CRITERIA = ("relevance", "faithfulness", "attribution", "time", "limitations", "readability")


def judgment():
    def side():
        return {"scores": {criterion: 4 for criterion in CRITERIA}, "problems": [], "critical_errors": [],
                "task_fulfillment": "complete", "honest_missing_input": True}
    return {"A": side(), "B": side(), "preference": "A", "reason": "Evidence matches."}


def test_blind_assignment_is_stable_and_uses_both_orders():
    rows = [bench.blind_order(str(i), 0, "seed") for i in range(20)]
    assert rows == [bench.blind_order(str(i), 0, "seed") for i in range(20)]
    assert {row["A"] for row in rows} == {"baseline", "candidate"}


def test_failed_retrieval_abstention_never_becomes_corpus_absence_or_task_success():
    answered = case()
    state = bench.answer_state(answered, {"any_reference_hit": False}, {"status": "unsupported"})
    assert state == "retrieval_missed_and_abstained"
    negative = case(refs=[])
    negative["expect"] = "unsupported"
    assert bench.answer_state(negative, {}, {"status": "unsupported"}) == "corpus_absence_expected"
    report = bench.generation_summary([{"id": "q", "repeat": 0, "answer": {"status": "unsupported"},
                                       "status_ok": False, "evidence_state": state, "seconds": .1}])
    assert len(report["status_mismatches"]) == 1
    assert report["evidence_states"][state] == 1


@pytest.mark.parametrize("mutation", ["missing-criterion", "bool-score", "unsubstantiated-critical"])
def test_judge_requires_the_common_rubric_and_concrete_critical_errors(monkeypatch, mutation):
    monkeypatch.setattr(bench, "rubric", lambda: {criterion: criterion for criterion in CRITERIA})
    value = judgment()
    if mutation == "missing-criterion":
        del value["A"]["scores"]["faithfulness"]
    elif mutation == "bool-score":
        value["A"]["scores"]["faithfulness"] = True
    else:
        value["A"]["critical_errors"] = [{"criterion": "attribution", "text": ""}]
    with pytest.raises(ValueError):
        bench.validate_judgment(value)


def test_paired_judge_sees_gold_and_actual_inputs_once_per_repeat(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    monkeypatch.setattr(bench, "rubric", lambda: {criterion: criterion for criterion in CRITERIA})
    gold = fixture(tmp_path / "data/context")
    output = tmp_path / ".local/judge.json"
    reports = {}
    for side in ("baseline", "candidate"):
        path = tmp_path / ".local" / (side + ".json")
        value = {"fixture_sha256": evidence.digest(gold.read_bytes()), "context_commit": "a" * 40,
                 "query_vector_sha256": "same", "answer_model": "same-model", "summary": {},
                 "inputs": {"q": {"route": "generate", "model_messages": [{"role": "user", "content": "original question"}],
                                   "sources": [{"id": "S1", "excerpt": "Only this excerpt reached this answer."}],
                                   "source_snapshots": [{"excerpt": "Hidden full text must not reach the judge as actual input."}]}},
                 "runs": [{"id": "q", "repeat": repeat, "answer": {"status": "unsupported", "summary": "Missing evidence"},
                           "evidence_state": "retrieval_missed_and_abstained"} for repeat in range(2)]}
        evidence.private_write(path, value)
        reports[side] = path
    captured = []
    def provider(request):
        payload = json.loads(request.content)
        captured.append(payload)
        value = judgment()
        value["A"]["critical_errors"] = [{"criterion": "faithfulness", "text": "A specific sentence conflicts with a specific excerpt."}]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}]})
    args = SimpleNamespace(output=output, baseline=reports["baseline"], candidate=reports["candidate"],
                           fixture=gold, split=None, cases=None, seed="seed", concurrency=2)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await bench.judge(args, "private-test-token", client)
    report = asyncio.run(run())
    assert len(captured) == 2 and report["summary"]["judged_runs"] == 2
    for payload in captured:
        shown = json.loads(payload["messages"][1]["content"])
        assert set(shown["answers"]) == {"A", "B"}
        assert all("model_messages" not in v["actual_model_input"] for v in shown["answers"].values())
        assert shown["independent_gold"][0]["anchors"] == ["乙的独立证据。"]
        assert "Hidden full text" not in json.dumps(shown)
        assert "baseline" not in json.dumps(shown) and "candidate" not in json.dumps(shown)
    for row in report["pairs"]:
        assert row["judgment"][row["order"]["A"]]["critical_errors"]
    assert "private-test-token" not in output.read_text()


def test_complete_validated_repeats_use_direct_production_functions_with_fake_provider(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    old = json.loads((root / "tests/fixtures/retrieval-eval.json").read_text())["items"][0]
    row = {**case(refs=[]), "question": old["question"], "intent": old["intent"], "expect": "any"}
    lock = json.loads((root / "data/context-lock.json").read_text())
    gold = tmp_path / "fixture.json"
    gold.write_text(json.dumps({"schema_version": 1, "context_commit": lock["source_commit"], "refs": {}, "cases": [row]}))
    impl = evidence.implementation(root)
    query = evidence.semantic_query(row, impl.semantic)
    cache = tmp_path / ".local/vectors.json"
    evidence.private_write(cache, {"schema_version": 1, "model": impl.semantic.EMBEDDING_MODEL, "dimensions": impl.semantic.DIMENSIONS,
                                   "queries": {evidence.digest(query.encode()): {"query": query, "vector": old["vector"]}}})
    output = tmp_path / ".local/answers.json"
    args = SimpleNamespace(output=output, implementation=root, fixture=gold, split=None, cases=None,
                           vectors_cache=cache, model="gpt-5", repeats=2, concurrency=2)
    requests = []
    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        value = {"status": "answered", "summary": "合成测试回答。", "sections": [
            {"heading": "合成测试判断", "body": "合成测试转述。[S1]", "source_ids": ["S1"], "kind": "synthesis"}],
                 "followups": [], "clarifying_questions": [], "limitations": "", "source_reasons": [], "slug": "synthetic-test"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}, "finish_reason": "stop"}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await bench.generate(args, "private-test-token", client)
    report = asyncio.run(run())
    assert len(requests) == 2 and report["summary"]["completed"] == 2
    assert all(run["validated_model_answer"]["sections"] for run in report["runs"])
    assert report["inputs"]["q"]["model_messages"] == requests[0]["messages"]
    assert report["inputs"]["q"]["sources"]
    assert "private-test-token" not in output.read_text() and output.stat().st_mode & 0o777 == 0o600


def test_full_answers_cannot_accidentally_be_written_to_public_docs(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "ROOT", tmp_path)
    with pytest.raises(ValueError, match=".local"):
        bench.require_private_output(tmp_path / "docs/answers.json")
