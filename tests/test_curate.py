"""Ops' automatic feed asks Builder for one question's public version. Synthetic only; the model is faked."""
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from server.admission import AdmissionError, CURATE_PURPOSE, derived_secret
from server.app import create_app
from server.curate import CURATE_HEADER, verify_curate_proof
from test_backend import context_pack  # noqa: F401 (fixture)

TOKEN = "synthetic-curate-provider-token"
KEY = "a" * 32


def request(**updates):
    return {"v": 1, "question": "我在某某公司做产品三年了，怎么判断自己是真的学会了？",
            "answer": {"summary": "看能不能独立用出来。[S1]",
                       "sections": [{"heading": "先看能不能用出来", "body": "合成正文[S1]", "source_ids": ["S1"], "kind": "source"}],
                       "sources": [{"id": "S1", "title": "合成文章", "reason": "合成理由"}],
                       "limitations": "", "followups": []},
            "topics": [{"key": KEY, "label": "学会还是看懂"}], **updates}


def signed(body: bytes, *, offset=30, purpose=CURATE_PURPOSE):
    secret = derived_secret(TOKEN, purpose)
    expiry = int(time.time()) + offset
    signature = hmac.new(secret.encode(), f"ask-curate:v1:{expiry}:{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{signature}"


def decision(**updates):
    return {"publish": True, "skip_reason": None, "topic_key": None, "topic_label": "学会还是看懂",
            "question": "怎么判断自己是真的学会了一项技能？", "summary": "看能不能独立用出来。[S1]",
            "sections": [{"heading": "先看能不能用出来", "body": "合成正文[S1]", "source_ids": ["S1"], "kind": "source"}],
            "limitations": "", "followups": ["怎么练习才有效？"], "source_reasons": [{"id": "S1", "reason": "讲了怎么判断学会"}], **updates}


def provider(*replies):
    seen = []
    def transport(req):
        seen.append(json.loads(req.content))
        reply = replies[min(len(seen), len(replies)) - 1]
        return httpx.Response(200, json={"choices": [{"message": {"content": reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)}, "finish_reason": "stop"}]})
    return seen, httpx.MockTransport(transport)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("AI_BUILDER_TOKEN", TOKEN)


def call(client, body: dict, proof=None):
    raw = json.dumps(body, ensure_ascii=False).encode()
    return client.post("/api/curate", content=raw, headers={CURATE_HEADER: proof or signed(raw), "Content-Type": "application/json"})


def test_proof_is_body_bound_purpose_bound_and_short_lived():
    body = b'{"v":1}'
    verify_curate_proof(TOKEN, body, signed(body))
    for proof in [signed(body + b" "), signed(body, offset=-1), signed(body, offset=120), "v1.123.abc", None,
                  signed(body).replace("v1.", "v2.")]:
        with pytest.raises(AdmissionError):
            verify_curate_proof(TOKEN, body, proof)
    # A key for another purpose (the quota store, which the website holds) cannot sign here.
    other = derived_secret(TOKEN, "ask-lizheng:quota-store:v1")
    expiry = int(time.time()) + 30
    forged = hmac.new(other.encode(), f"ask-curate:v1:{expiry}:{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
    with pytest.raises(AdmissionError):
        verify_curate_proof(TOKEN, body, f"v1.{expiry}.{forged}")


def test_publishes_a_checked_public_version(context_pack, token):
    seen, transport = provider(decision())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        response = call(client, request())
    assert response.status_code == 200
    body = response.json()
    assert body["publish"] is True and body["question"] == "怎么判断自己是真的学会了一项技能？"
    assert body["topic_key"] is None and body["topic_label"] == "学会还是看懂"
    assert body["source_reasons"] == [{"id": "S1", "reason": "讲了怎么判断学会"}]
    # The model saw the question and the published topics, nothing else about the asker.
    sent = json.loads(seen[0]["messages"][1]["content"])
    assert set(sent) == {"v", "question", "answer", "topics"}


def test_reusing_a_topic_keeps_its_name(context_pack, token):
    _, transport = provider(decision(topic_key=KEY, topic_label="别的名字"))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        body = call(client, request()).json()
    assert body["topic_key"] == KEY and body["topic_label"] == "学会还是看懂"


def test_a_decline_returns_only_its_reason(context_pack, token):
    _, transport = provider({**decision(publish=False, skip_reason="personal"), "question": "", "summary": "", "sections": []})
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).json() == {"publish": False, "skip_reason": "personal"}


def test_one_repair_then_give_up(context_pack, token):
    # An unknown source id, then a fixed reply: repaired once.
    seen, transport = provider(decision(sections=[{"heading": "h", "body": "b", "source_ids": ["S9"], "kind": "source"}]), decision())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).json()["publish"] is True
    assert len(seen) == 2 and "上面的输出不符合要求" in seen[1]["messages"][-1]["content"]
    # Two bad replies: no public version.
    seen, transport = provider("not json", decision(topic_key="b" * 32))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        response = call(client, request())
    assert response.status_code == 503 and response.json() == {"code": "invalid_answer"} and len(seen) == 2


@pytest.mark.parametrize("bad", [
    decision(topic_label="一个非常非常长的新主题名称超过十个字"),
    decision(question=""),
    decision(sections=[]),
    decision(publish=False, skip_reason=None),
    {**decision(), "extra": 1},
])
def test_rejects_incomplete_or_invented_output(context_pack, token, bad):
    _, transport = provider(bad, bad)
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).status_code == 503


def test_unsigned_or_malformed_requests_never_reach_the_model(context_pack, token):
    seen, transport = provider(decision())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        raw = json.dumps(request()).encode()
        assert client.post("/api/curate", content=raw).status_code == 403
        assert call(client, request(), proof=signed(b"other")).status_code == 403
        assert call(client, {**request(), "visitor_id": "x"}).status_code == 422
        assert call(client, request(topics=[{"key": "short", "label": "x"}])).status_code == 422
    assert not seen
