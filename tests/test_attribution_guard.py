"""Structural production checks and explicitly offline attribution diagnostics."""
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


@pytest.mark.parametrize("denial", ["这是一位嘉宾的观点，不是立正本人的判断。", "这里明确区分了嘉宾的判断与立正的观点，没有替他表示赞同。"])
def test_semantic_regex_cannot_reject_a_correct_attribution_denial(passages, clock, monkeypatch, denial):
    sid = guest_id(passages)
    calls = []
    def forbidden(*args): raise AssertionError("semantic regex reached production")
    monkeypatch.setattr(answers, "misattributed", forbidden)
    def provider(request):
        calls.append(request)
        return response(answer_for(sid, denial + f"[{sid}]"))
    result=generate(provider,passages)
    assert result.sections[0].body == denial + f"[{sid}]"
    assert len(calls)==1


def test_nonexistent_source_still_requires_structural_repair(passages, clock):
    calls=[];sid=passages[0].source["id"]
    def provider(request):
        calls.append(request)
        return response(answer_for("S99" if len(calls)==1 else sid,"结构检查。"))
    result=generate(provider,passages)
    assert result.sections[0].source_ids == [sid]
    assert len(calls)==2


@pytest.mark.parametrize("remaining", [40.0,1.0])
def test_valid_denial_does_not_spend_remaining_budget_on_a_guessy_rewrite(passages,clock,remaining):
    sid=guest_id(passages);calls=[]
    def provider(request):
        calls.append(request);clock[0]=180.0-remaining
        return response(answer_for(sid,f"这是嘉宾的论点，不是立正本人的判断。[{sid}]"))
    assert generate(provider,passages).status == "answered"
    assert len(calls)==1


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


def test_correct_denial_streams_without_semantic_regex(passages,monkeypatch):
    sid=guest_id(passages);answer=answer_for(sid,f"这是嘉宾观点，不是立正本人的判断。[{sid}]")
    def forbidden(*args): raise AssertionError("semantic regex reached partial production validation")
    monkeypatch.setattr(answers,"misattributed",forbidden)
    assert answers.completed_parts(answer.model_dump_json(),passages) == (answer.summary, answer.sections)


def test_stream_still_blocks_an_unknown_source_binding(passages):
    answer=answer_for("S99","不存在的出处。")
    assert answers.completed_parts(answer.model_dump_json(),passages)[1] == []


@pytest.mark.parametrize("field", ["body", "followup", "reason", "indirect-followup"])
def test_programme_guest_alias_is_not_a_fragment_speaker(passages, field):
    p = next(p for p in passages if p.source["author"] == "Guest Author")
    p.evidence["participant_aliases"] = "Synthetic Alpha"
    p.source.update(speaker_id="unknown", speaker_status="unresolved")
    sid = p.source["id"]
    answer = answer_for(sid, f"节目讨论了合成问题。[{sid}]。")
    if field == "body": answer.sections[0].body = f"Synthetic Alpha认为合成结论成立。[{sid}]。"
    if field == "followup": answer.followups = ["Synthetic Alpha说的合成结论是什么？"]
    if field == "reason": answer.source_reasons = [answers.SourceReason(source_id=sid, reason="Synthetic Alpha的观点集中在这里。") ]
    if field == "indirect-followup": answer.followups = ["Synthetic Alpha对合成问题给了什么具体方法？"]
    assert answers.misattributed(answer, passages)


def test_named_confirmed_guest_statement_is_supported(passages):
    p = next(p for p in passages if p.source["author"] == "Guest Author")
    p.evidence.update(participant_aliases="Synthetic Alpha", speech_act="statement")
    p.source.update(speaker_id="guest:synthetic-alpha", speaker_name="Synthetic Alpha", speaker_status="reviewed-source-attribution")
    sid = p.source["id"]
    answer = answer_for(sid, f"Synthetic Alpha认为合成结论成立。[{sid}]。")
    assert not answers.misattributed(answer, passages)
    p.evidence["speech_act"] = "question"
    assert answers.misattributed(answer, passages)
    answer.sections[0].body = f"不能确认Synthetic Alpha的观点。[{sid}]。"
    assert not answers.misattributed(answer, passages)
