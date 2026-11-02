"""Spending boundaries are exercised only against fake billing/model endpoints."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import answer_benchmark as bench
from scripts import experiment_guard as guard
from test_evidence_benchmark import fixture

URL = "https://space.ai-builders.com/backend/v1/chat/completions"
PAYLOAD = {"model": "fake-model", "messages": [{"role": "user", "content": "PRIVATE TEST INPUT"}], "max_tokens": 100}


def experiment(tmp_path, **updates):
    root = tmp_path / "checkout"
    path = root / ".local/plan.json"
    plan = {"id": "fake-experiment", "decision": "Keep or reject this change", "uncertainty": "Does it fix the failure?",
            "decision_signal": "Correct the failure without breaking the control", "stop_condition": "Stop after this comparison",
            "case_ids": ["q"], "max_cases": 1, "max_repeats": 1, "max_requests": 2,
            "budget_usd": 1, "estimated_total_usd": .5, "reservation_usd_per_request": {"fake-model": .25},
            "max_request_bytes": 1000, "max_output_tokens": 100, **updates}
    guard.private_json(path, plan)
    return guard.Experiment(path, root=root, state_root=tmp_path / "shared-state"), path


def test_default_cli_does_not_even_enter_generation_with_a_credential(tmp_path, monkeypatch, capsys):
    gold = fixture(tmp_path / "data/context")
    monkeypatch.setenv("AI_BUILDER_TOKEN", "fake-never-use")
    def forbidden(*args, **kwargs):
        pytest.fail("Offline preview entered generation")
    monkeypatch.setattr(bench, "generate", forbidden)
    args = ["generate", "--fixture", str(gold), "--output", str(tmp_path / ".local/output.json")]
    bench.main(args)
    preview = json.loads(capsys.readouterr().out)
    assert preview["network_requests"] == 0 and preview["repeats"] == 1
    assert not (tmp_path / ".local/output.json").exists()
    with pytest.raises(SystemExit, match="--experiment-plan"):
        bench.main([*args, "--execute"])


def test_scope_requires_an_actionable_plan_and_explanation_for_expansion(tmp_path):
    exp, _ = experiment(tmp_path, max_repeats=3)
    exp.scope(["q"])
    for ids, repeats, flags in [(["other"], 1, {}), (["q"], 3, {}), (["q"], 1, {"fresh": True}),
                                 (["q"], 1, {"retry": True}), ([], 1, {})]:
        with pytest.raises(ValueError):
            exp.scope(ids, repeats, **flags)
    with pytest.raises(ValueError):
        experiment(tmp_path, decision_signal="")
    with pytest.raises(ValueError):
        experiment(tmp_path, estimated_total_usd=2)


def test_shared_budget_counts_streams_and_concurrent_stages_without_reset(tmp_path):
    exp, plan = experiment(tmp_path)
    other_root = tmp_path / "another-checkout"
    other_plan = other_root / ".local/plan.json"
    guard.private_json(other_plan, json.loads(plan.read_text()))
    other = guard.Experiment(other_plan, root=other_root, state_root=tmp_path / "shared-state")
    sent = []
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"lifetime_cost_usd": 10}))) as meter:
            async def send(index):
                transport = guard.BudgetTransport((exp, other)[index % 2], "FAKE SECRET", ("generate", "judge")[index % 2],
                    meter=meter, inner=httpx.MockTransport(lambda r: (sent.append(r.content), httpx.Response(200, json={}))[1]))
                async with httpx.AsyncClient(transport=transport) as client:
                    return await client.post(URL, json={**PAYLOAD, "stream": index == 0}, headers={"Authorization": "Bearer FAKE SECRET"})
            return await asyncio.gather(*(send(i) for i in range(6)), return_exceptions=True)
    results = asyncio.run(run())
    assert len(sent) == 2 and sum(isinstance(r, guard.ExperimentStop) for r in results) == 4
    state = other.receipt()
    assert state["requests"] == 2 and state["reserved_usd"] == .5 and state["stopped"]
    private = "".join(p.read_text() for p in exp.directory.glob("*.json"))
    assert "FAKE SECRET" not in private and "PRIVATE TEST INPUT" not in private
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in exp.directory.iterdir())
    changed = json.loads(plan.read_text())
    changed["max_requests"] = 100
    guard.private_json(plan, changed)
    reset = guard.Experiment(plan, root=exp.root, state_root=tmp_path / "shared-state")
    with pytest.raises(guard.ExperimentStop, match="cannot reset"):
        reset.reserve(PAYLOAD, b"{}", 10, "attempted-reset")


@pytest.mark.parametrize("failure", ["size", "output", "model", "meter", "auth", "account-spend", "reservation", "rate"])
def test_failure_stops_later_calls_without_hidden_retry(tmp_path, failure):
    exp, _ = experiment(tmp_path, budget_usd=1 if failure == "account-spend" else .5, estimated_total_usd=.5,
                        reservation_usd_per_request={"fake-model": .3})
    sent = []
    costs = iter([10.8, 10.8, 10.8] if failure == "account-spend" else [10, 10.4, 10.4])
    def meter_response(request):
        return httpx.Response(503) if failure == "meter" else httpx.Response(200, json={"lifetime_cost_usd": next(costs)})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(meter_response)) as meter:
            inner = httpx.MockTransport(lambda r: (sent.append(r.content), httpx.Response(429 if failure == "rate" else 200, json={}))[1])
            async with httpx.AsyncClient(transport=guard.BudgetTransport(exp, "fake", "test", inner=inner, meter=meter)) as client:
                payload = dict(PAYLOAD)
                if failure == "size": payload["messages"] = [{"content": "x" * 1001}]
                if failure == "output": payload["max_tokens"] = 101
                if failure == "model": payload["model"] = "not-allowed"
                headers = {"Authorization": "Bearer " + ("wrong" if failure == "auth" else "fake")}
                if failure in {"account-spend", "reservation"}:
                    # Establish a baseline and consume one reservation without an actual model call.
                    exp.reserve(PAYLOAD, b"{}", 10 if failure == "account-spend" else 10.4, "prior")
                with pytest.raises(guard.ExperimentStop):
                    await client.post(URL, json=payload, headers=headers)
                with pytest.raises(guard.ExperimentStop):
                    await client.post(URL, json=PAYLOAD, headers={"Authorization": "Bearer fake"})
    asyncio.run(run())
    assert len(sent) == (1 if failure == "rate" else 0)
    assert exp.receipt()["stopped"]
    if failure == "account-spend":
        assert "Reported account spend" in exp.receipt()["stopped"]


def test_conclusion_closes_experiment_and_plain_clients_cannot_bypass_guard(tmp_path):
    exp, _ = experiment(tmp_path)
    exp.conclude("reject", "The control regressed; another run would not change rejection.")
    with pytest.raises(guard.ExperimentStop, match="concluded"):
        exp.reserve(PAYLOAD, b"{}", 0, "after-finish")
    with pytest.raises(ValueError, match="budgeted"):
        guard.require_guarded(SimpleNamespace(_transport=object()))

