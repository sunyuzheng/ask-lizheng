import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from server.evidence_selection import select_evidence
from server.app import create_app
from test_backend import answer_for, context_pack, events, index

pytestmark = pytest.mark.evidence_selection


@pytest.mark.parametrize("value", [{"source_ids":["S99"]}, {"source_ids":"S1"},
    {"source_ids":["S1","S1"]}, {"source_ids":[1]}, {"source_ids":["S1"],"facts":"invented"}, {}])
def test_invalid_selection_retains_original_eligible_candidates(index, value):
    passages=index.retrieve("职业选择")
    def provider(request):
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(value)}}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await select_evidence(client,"synthetic-token","gpt-5",{"question":"职业选择"},passages)
    assert asyncio.run(run()) is passages


@pytest.mark.parametrize("empty",[False,True])
def test_api_selects_actual_evidence_before_generation_without_changing_records(context_pack,monkeypatch,empty):
    monkeypatch.setenv("AI_BUILDER_TOKEN","synthetic-token")
    monkeypatch.setenv("AI_MODEL","gpt-5")
    seen=[]
    def provider(request):
        payload=json.loads(request.content);seen.append(payload)
        user=json.loads(payload["messages"][1]["content"])
        assert not {"query_log_notice","conversation_id","ai_consent_model","ai_consent_backup"} & set(user)
        if "stream" not in payload:
            assert len(user["sources"])>=2
            assert not {"url","source_path","docindex"} & set(user["sources"][0])
            return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps({"source_ids":[] if empty else ["S2"]})}}]})
        assert [s["id"] for s in user["sources"]]==["S2"]
        return httpx.Response(200,json={"choices":[{"message":{"content":answer_for("S2").model_dump_json()},"finish_reason":"stop"}]})
    with TestClient(create_app(context_pack,httpx.MockTransport(provider))) as client:
        result=events(client.post("/api/ask",json={"question":"职业选择怎么做"}))[-1][1]
    assert result["status"]==("unsupported" if empty else "answered")
    assert len(seen)==(1 if empty else 2)
    assert len(result["sources"])==(0 if empty else 1)


def test_selection_network_failure_keeps_candidates(index):
    passages=index.retrieve("职业选择")
    def provider(request):raise httpx.ReadTimeout("synthetic")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            return await select_evidence(client,"synthetic-token","gpt-5",{"question":"职业选择"},passages)
    assert asyncio.run(run()) is passages
