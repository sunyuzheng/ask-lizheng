"""Synthetic provider regressions for fail-closed source attribution."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from server import answers
from test_backend import answer_for, context_pack, index


@pytest.fixture
def passages(index):
    return index.retrieve("职业选择如何积累能力与作品")


def guest_id(passages):
    return next(p.source["id"] for p in passages if p.source["author"] == "Guest Author")


def response(answer):
    return httpx.Response(200, json={"choices": [{"message": {"content": answer.model_dump_json()}, "finish_reason": "stop"}]})


def generate(provider, passages, **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await answers.generate_answer(client, "synthetic-placeholder-token", "gpt-5", {"question": "测试归属"}, passages, **kwargs)
    return asyncio.run(run())


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(answers, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(answers, "ANSWER_BUDGET_SECONDS", 80)
    return now


def test_both_attempts_misattributed_fail_closed(passages, clock):
    sid = guest_id(passages)
    calls = []
    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 2:
            assert "不是立正本人的材料" in payload["messages"][-1]["content"]
        return response(answer_for(sid, f"立正提到，先评估生活约束 [{sid}]。"))
    with pytest.raises(answers.ProviderFailure) as exc:
        generate(provider, passages)
    assert exc.value.code == "invalid_answer"
    assert len(calls) == 2


def test_first_attempt_can_be_repaired_once(passages, clock):
    sid = guest_id(passages)
    calls = []
    def provider(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return response(answer_for(sid, f"立正提到，先评估生活约束 [{sid}]。"))
        assert "不是立正本人的材料" in payload["messages"][-1]["content"]
        return response(answer_for(sid, f"一位作者提到，先评估生活约束 [{sid}]。"))
    result = generate(provider, passages)
    assert "立正" not in result.sections[0].body
    assert len(calls) == 2


@pytest.mark.parametrize("remaining", [40.0, 1.0])
def test_insufficient_repair_budget_rejects_without_retry(passages, clock, remaining):
    sid = guest_id(passages)
    calls = []
    def provider(request):
        calls.append(request)
        clock[0] = 180.0 - remaining
        return response(answer_for(sid, f"立正提到，先评估生活约束 [{sid}]。"))
    with pytest.raises(answers.ProviderFailure) as exc:
        generate(provider, passages)
    assert exc.value.code == "invalid_answer"
    assert len(calls) == 1


@pytest.mark.parametrize("origin", ["yuzheng-published-text", "yuzheng-spoken-source"])
def test_own_source_named_answer_passes_without_repair(passages, clock, origin):
    own = next(p for p in passages if p.source["author"] == "Yuzheng Sun" and not p.discovery)
    own.evidence["content_origin"] = origin
    sid = own.source["id"]
    calls = []
    def provider(request):
        calls.append(request)
        return response(answer_for(sid, f"立正提到，用作品验证能力 [{sid}]。"))
    result = generate(provider, passages)
    assert result.sections[0].body == f"立正提到，用作品验证能力 [{sid}]。"
    assert len(calls) == 1


def test_misattributed_stream_section_is_never_exposed(passages):
    sid = guest_id(passages)
    answer = answer_for(sid, f"立正提到，先评估生活约束 [{sid}]。")
    assert answers.completed_sections(answer.model_dump_json(), passages) == []


def test_stream_keeps_prior_valid_section_but_not_misattributed_section(passages):
    sid = guest_id(passages)
    answer = answer_for(sid, f"一位作者提到，先评估生活约束 [{sid}]。")
    wrong = answer_for(sid, f"立正提到，先评估生活约束 [{sid}]。").sections[0]
    answer.sections.append(wrong)
    assert answers.completed_sections(answer.model_dump_json(), passages) == [answer.sections[0]]
