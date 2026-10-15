"""Ops' automatic feed asks Builder for the topic of one question it publishes. Synthetic only; the model is faked."""
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
    return {"v": 3, "question": "我在某某公司做产品三年了，怎么判断自己是真的学会了？",
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


def judgment(**updates):
    return {"topic_key": None, "topic_label": "学会还是看懂", **updates}


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
    body = b'{"v":3}'
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


def test_only_names_the_topic_and_never_judges(context_pack, token):
    # 2026-10-15: every answered question is published as asked; the model only files it under a topic.
    seen, transport = provider(judgment())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        body = call(client, request()).json()
    assert body == {"topic_key": None, "topic_label": "学会还是看懂"}
    system = seen[0]["messages"][0]["content"]
    assert "原样公开" in system and "不判断要不要公开" in system and "skip_reason" not in system and seen[0]["max_tokens"] == 400
    # The model saw the question, its answer and the published topics, nothing about the asker.
    assert set(json.loads(seen[0]["messages"][1]["content"])) == {"v", "question", "answer", "topics"}
    # An existing topic keeps its name.
    _, transport = provider(judgment(topic_key=KEY, topic_label="别的名字"))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).json() == {"topic_key": KEY, "topic_label": "学会还是看懂"}


def test_names_the_word_of_a_page_still_to_make(context_pack, token):
    # A question answered before every answer had its page: Ops asks for its address word as well.
    seen, transport = provider(judgment(word="Career Choice, Data Science!"))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        body = call(client, request(word=True)).json()
    assert body == {"topic_key": None, "topic_label": "学会还是看懂", "word": "career-choice-data-science"}
    assert "word" in seen[0]["messages"][0]["content"] and "word" not in json.loads(seen[0]["messages"][1]["content"])
    # Asked for, it must come; not asked for, it is not passed on.
    seen, transport = provider(judgment(), judgment(word="fake-work"))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request(word=True)).json()["word"] == "fake-work"
    assert len(seen) == 2
    _, transport = provider(judgment(word="fake-work"))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).json() == {"topic_key": None, "topic_label": "学会还是看懂"}


@pytest.mark.parametrize("bad", [
    judgment(topic_key="b" * 32, topic_label="主题"),
    judgment(topic_label=""),
    judgment(topic_label="一个非常非常长的新主题名称超过十个字"),
    # No judging any more: a decline is not an answer.
    {"publish": False, "skip_reason": "personal", "topic_key": None, "topic_label": ""},
    # The model never rewrites the question.
    judgment(question="改写过的问题"),
])
def test_rejects_anything_but_a_topic(context_pack, token, bad):
    _, transport = provider(bad, bad)
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).status_code == 503


def test_one_repair_then_give_up(context_pack, token):
    # An unknown topic key, then a fixed reply: repaired once.
    seen, transport = provider(judgment(topic_key="b" * 32), judgment())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).json()["topic_label"] == "学会还是看懂"
    assert len(seen) == 2 and "上面的输出不符合要求" in seen[1]["messages"][-1]["content"]
    # Two bad replies: nothing to publish.
    seen, transport = provider("not json", judgment(topic_key="b" * 32))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        response = call(client, request())
    assert response.status_code == 503 and response.json() == {"code": "invalid_answer"} and len(seen) == 2


def test_unsigned_or_malformed_requests_never_reach_the_model(context_pack, token):
    seen, transport = provider(judgment())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        raw = json.dumps(request()).encode()
        assert client.post("/api/curate", content=raw).status_code == 403
        assert call(client, request(), proof=signed(b"other")).status_code == 403
        assert call(client, {**request(), "visitor_id": "x"}).status_code == 422
        assert call(client, request(topics=[{"key": "short", "label": "x"}])).status_code == 422
        # The de-identifying version (v1, gone 2026-10-14) and the judging one (v2, gone 2026-10-15).
        assert call(client, request(v=1)).status_code == 422
        assert call(client, request(v=2)).status_code == 422
    assert not seen


def signed_for(path, body: bytes):
    secret = derived_secret(TOKEN, CURATE_PURPOSE)
    expiry = int(time.time()) + 30
    signature = hmac.new(secret.encode(), f"ask-curate:v1:{path}:{expiry}:{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{signature}"


def test_a_proof_for_another_route_is_refused_and_the_first_batch_routes_are_gone(context_pack, token):
    seen, transport = provider(judgment())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        raw = json.dumps(request(), ensure_ascii=False).encode()
        assert call(client, request(), proof=signed_for("/api/themes", raw)).status_code == 403
        for path in ("/api/themes", "/api/seed-answer"):
            assert client.post(path, content=raw, headers={CURATE_HEADER: signed_for(path, raw)}).status_code in {404, 405}
    assert not seen
