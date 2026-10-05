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


def test_v2_publishes_as_asked_and_only_names_the_topic(context_pack, token):
    # 2026-10-05: questions asked under the talk wording are published as asked; the model only judges.
    seen, transport = provider({"publish": True, "skip_reason": None, "topic_key": None, "topic_label": "学会还是看懂"})
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        body = call(client, request(v=2)).json()
    assert body == {"publish": True, "topic_key": None, "topic_label": "学会还是看懂"}
    system = seen[0]["messages"][0]["content"]
    assert "原样公开" in system and "去掉一切可能认出" not in system and seen[0]["max_tokens"] == 400
    # An existing topic keeps its name; a decline is only its reason.
    _, transport = provider({"publish": True, "skip_reason": None, "topic_key": KEY, "topic_label": "别的名字"})
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request(v=2)).json() == {"publish": True, "topic_key": KEY, "topic_label": "学会还是看懂"}
    _, transport = provider({"publish": False, "skip_reason": "personal", "topic_key": None, "topic_label": ""})
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request(v=2)).json() == {"publish": False, "skip_reason": "personal"}


@pytest.mark.parametrize("bad", [
    {"publish": True, "skip_reason": None, "topic_key": "b" * 32, "topic_label": "主题"},
    {"publish": True, "skip_reason": None, "topic_key": None, "topic_label": ""},
    {"publish": False, "skip_reason": None, "topic_key": None, "topic_label": ""},
    # A v1 public version is not a v2 reply: v2 never rewrites the question.
    {"publish": True, "skip_reason": None, "topic_key": None, "topic_label": "主题", "question": "改写过的问题"},
])
def test_v2_rejects_anything_but_a_decision_and_topic(context_pack, token, bad):
    _, transport = provider(bad, bad)
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request(v=2)).status_code == 503


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


def test_a_number_written_as_a_name_is_sent_back_once_then_titled(context_pack, token):
    named = decision(sections=[{"heading": "先看能不能用出来", "body": "S1 说得更直接：能用出来才算学会。", "source_ids": ["S1"], "kind": "source"}],
                     source_reasons=[{"id": "S1", "reason": "可对照S1的例子。"}])
    seen, transport = provider(named, decision())
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        assert call(client, request()).json()["sections"][0]["body"] == "合成正文[S1]"
    assert len(seen) == 2 and "「S1 说得更直接：能用出来才算学会。」" in seen[1]["messages"][-1]["content"]
    # The same again, or a rewrite that fails: published, with the source's title where its number was.
    for second in (named, "not json"):
        _, transport = provider(named, second)
        with TestClient(create_app(context_pack, provider_transport=transport)) as client:
            body = call(client, request()).json()
        assert body["publish"] is True and body["sections"][0]["body"] == "《合成文章》[S1]说得更直接：能用出来才算学会。"
        assert body["source_reasons"] == [{"id": "S1", "reason": "可对照《合成文章》的例子。"}]


def test_a_mark_without_its_letter_is_published_with_it(context_pack, token):
    _, transport = provider(decision(summary="看能不能独立用出来。[1]",
                                     sections=[{"heading": "先看能不能用出来", "body": "合成正文 [1]。", "source_ids": ["S1"], "kind": "source"}]))
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        body = call(client, request()).json()
    assert body["summary"] == "看能不能独立用出来。[S1]" and body["sections"][0]["body"] == "合成正文 [S1]。"


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



def signed_for(path, body: bytes):
    secret = derived_secret(TOKEN, CURATE_PURPOSE)
    expiry = int(time.time()) + 30
    signature = hmac.new(secret.encode(), f"ask-curate:v1:{path}:{expiry}:{hashlib.sha256(body).hexdigest()}".encode(), hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{signature}"


def post_feed(client, path, body: dict, proof=None):
    raw = json.dumps(body, ensure_ascii=False).encode()
    return client.post(path, content=raw, headers={CURATE_HEADER: proof or signed_for(path, raw), "Content-Type": "application/json"})


ASKED = ["我在某某公司做产品三年，怎么判断自己是真的学会了AI？", "学了很多AI工具，怎么知道自己真的会了？", "用AI后效率高了，为什么工资没涨？"]


def test_themes_return_fresh_generic_questions_only(context_pack, token):
    reply = {"themes": [
        {"topic_label": "学会还是看懂", "questions": ["学了很多工具以后，怎样确认自己真的掌握了，而不只是看懂？",
                                                   "怎么判断自己是真的学会了AI？"], "count": 2},
        {"topic_label": "AI提效与价值", "questions": ["效率提高了，为什么价值没有跟着变？"], "count": 1},
        {"topic_label": "学会还是看懂", "questions": ["同一个主题的第二组问题，应该被去掉？"], "count": 2},
        {"topic_label": "一个明显超过十四个字的主题名称实在太长了", "questions": ["主题名太长的问题会被去掉吗？"], "count": 3},
        {"topic_label": "已有主题", "questions": ["已经发布过的主题不再重复？"], "count": 4},
    ]}
    # The question that repeats an asker's words gets one rewrite.
    rewrite = {"rewrites": [{"topic_label": "学会还是看懂", "question": "掌握一项技能和仅仅理解它，区别在哪里？"}]}
    seen, transport = provider(reply, rewrite)
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        response = post_feed(client, "/api/themes", {"v": 1, "questions": ASKED, "existing": ["已有主题"]})
    assert response.status_code == 200
    assert response.json() == {
        "themes": [{"topic_label": "学会还是看懂", "question": "学了很多工具以后，怎样确认自己真的掌握了，而不只是看懂？", "count": 2},
                   {"topic_label": "学会还是看懂", "question": "掌握一项技能和仅仅理解它，区别在哪里？", "count": 2}],
        "report": {"proposed": 5, "rare": 1, "label": 1, "repeat": 2, "borrowed": 0, "rewritten": 1}}
    assert "不能公开" in seen[0]["messages"][0]["content"]
    assert "已有主题" in seen[0]["messages"][1]["content"]
    assert len(seen) == 2


def test_a_rewrite_that_still_echoes_is_dropped(context_pack, token):
    reply = {"themes": [{"topic_label": "学会还是看懂", "questions": ["怎么判断自己是真的学会了AI？"], "count": 3}]}
    rewrite = {"rewrites": [{"topic_label": "学会还是看懂", "question": "怎么判断自己是真的学会了AI呢？"}]}
    _, transport = provider(reply, rewrite)
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        body = post_feed(client, "/api/themes", {"v": 1, "questions": ASKED}).json()
    assert body["themes"] == [] and body["report"]["borrowed"] == 1


def test_feed_proofs_are_bound_to_their_route(context_pack, token):
    seen, transport = provider({"themes": []})
    with TestClient(create_app(context_pack, provider_transport=transport)) as client:
        raw = json.dumps({"v": 1, "questions": ASKED}, ensure_ascii=False).encode()
        assert post_feed(client, "/api/themes", {"v": 1, "questions": ASKED}, proof=signed(raw)).status_code == 403
        assert post_feed(client, "/api/themes", {"v": 1, "questions": ASKED}, proof=signed_for("/api/seed-answer", raw)).status_code == 403
        assert post_feed(client, "/api/themes", {"v": 1, "questions": []}).status_code == 422
    assert not seen


def test_seed_answer_answers_like_ask_without_records(context_pack, token, monkeypatch):
    from test_backend import answer_for
    seen = []
    async def generate(client, token, model, request, passages, **callbacks):
        seen.append(request)
        return answer_for()
    monkeypatch.setattr("server.app.generate_answer", generate)
    stored = []
    app = create_app(context_pack, query_record_transport=httpx.MockTransport(lambda request: stored.append(request) or httpx.Response(200, json={"ok": True})))
    with TestClient(app) as client:
        response = post_feed(client, "/api/seed-answer", {"v": 1, "question": "职业选择怎么做？"})
        assert response.status_code == 200, response.text
        answer = response.json()["answer"]
        assert answer["status"] == "answered" and answer["sources"]
        assert app.state.slots._value == 3
    assert seen[0]["context"] == "" and seen[0]["history"] == [] and seen[0]["intent"] == "understand"
    assert not stored


def test_seed_answer_waits_for_readers(context_pack, token, monkeypatch):
    async def generate(*args, **kwargs): pytest.fail("no free slot, no model call")
    monkeypatch.setattr("server.app.generate_answer", generate)
    app = create_app(context_pack)
    with TestClient(app) as client:
        for _ in range(3):
            app.state.slots._value -= 1
        assert post_feed(client, "/api/seed-answer", {"v": 1, "question": "职业选择怎么做？"}).json() == {"code": "provider_busy"}
