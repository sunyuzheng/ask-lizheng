"""Ops' automatic feed: the topic each published question is filed under.

Every question answered under the v4 notice is published as asked, with its answer (2026-10-15, the
owner: 「不要AI判断给不给别人看，全都放」, the situation and follow-ups included; the asker was told
it is like raising a hand at a recorded talk, public with no names, and that the answer may mention
the note passed to the stage). Ops sends one question with its archived answer and the topics
already published; the model only files it under one of them or names a new one, so 最常问 can count
what is asked often. For a question that has no page yet (answered before lizheng.ai made one for
every answer), Ops also asks for the word of its page's address. Ops publishes what comes back; the
owner can withdraw any item there.

Until 2026-10-15 the model also judged whether a question was worth showing (v2), and until
2026-10-14 wrote de-identified public versions (v1) and a first batch of themes (/api/themes,
/api/seed-answer); all of that is gone.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .admission import CURATE_PURPOSE, AdmissionError, derived_secret
from .answers import ProviderFailure, model_options, provider_status
from .ops_records import share_word

CURATE_HEADER = "x-ask-curate-proof"
CURATE_BODY_LIMIT = 262144
CURATE_SECONDS = 75
SOURCE_ID = r"^S\d{1,2}$"


class CurateSource(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(pattern=SOURCE_ID)
    title: str = Field(max_length=300)
    reason: str = Field(default="", max_length=800)
    author: str = Field(default="", max_length=300)
    attribution_note: str = Field(default="", max_length=2500)
    data_layer: str = Field(default="", max_length=20)
    speaker_id: str = Field(default="", max_length=160)
    speaker_name: str = Field(default="", max_length=160)
    speaker_status: str = Field(default="", max_length=100)
    fragment_id: str = Field(default="", max_length=300)
    source_snapshot_sha256: str = Field(default="", max_length=64)
    participant_names: str = Field(default="", max_length=1000)
    participant_entity_ids: str = Field(default="", max_length=1000)
    participant_aliases: str = Field(default="", max_length=1000)
    participant_metadata_basis: str = Field(default="", max_length=1000)
    speech_act: str = Field(default="", max_length=100)
    content_origin: str = Field(default="", max_length=100)
    generation_method: str = Field(default="", max_length=100)


class CurateSection(BaseModel):
    model_config = ConfigDict(extra="ignore")
    heading: str = Field(max_length=200)
    body: str = Field(max_length=4000)
    source_ids: list[str] = Field(default_factory=list, max_length=12)
    kind: Literal["source", "synthesis", "application"] = "synthesis"


class CurateAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = Field(max_length=2000)
    sections: list[CurateSection] = Field(max_length=10)
    sources: list[CurateSource] = Field(max_length=16)
    limitations: str = Field(default="", max_length=1500)
    followups: list[str] = Field(default_factory=list, max_length=6)


class CurateTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-f0-9]{32}$")
    label: str = Field(min_length=1, max_length=80)


class CurateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[3]
    question: str = Field(min_length=1, max_length=2000)
    answer: CurateAnswer
    topics: list[CurateTopic] = Field(default_factory=list, max_length=300)
    # The question has no page yet: name the word of its address too.
    word: bool = False


class TopicDecision(BaseModel):
    """v3: the topic, and the address word when it was asked for."""
    model_config = ConfigDict(extra="forbid")
    topic_key: str | None
    topic_label: str = Field(max_length=80)
    word: str = Field(default="", max_length=120)


TOPIC_PROMPT = """你在为「问问立正」的公开问答归主题。每个回答过的问题都会和它的回答一起原样公开，你不判断要不要公开，也不改写问题。
你会收到一个问题、它的回答（按段落，带出处编号S1、S2…）和已经公开的主题列表。已有主题如果和这个问题问的是同一件事，topic_key 填那个 key、topic_label 填它的名称；否则 topic_key 填 null，起一个新的主题名，不超过10个字，说清问的是什么，例如「学会还是看懂」「AI提效与工作价值」。
只输出一个 JSON 对象：{"topic_key": 已有key或null, "topic_label": "主题名"}。"""

WORD_PROMPT = """
这个问题还要一个网页地址用的词，多输出一个字段 word：1 至 3 个小写英文单词概括问题的话题，用连字符连接，如 career-choice、fake-work、ai-learning；只写话题，不写人名、公司名、地名或其他能认出提问者的信息。这时输出 {"topic_key": …, "topic_label": …, "word": "…"}。"""


def verify_curate_proof(token: str, body: bytes, proof: str | None, now: float | None = None, path: str = "/api/curate") -> None:
    secret = derived_secret(token, CURATE_PURPOSE)
    if not isinstance(proof, str) or not re.fullmatch(r"v1\.[0-9]{10}\.[a-f0-9]{64}", proof):
        raise AdmissionError("invalid_admission", 403)
    _, expiry, signature = proof.split(".")
    current = int(now if now is not None else time.time())
    if int(expiry) <= current or int(expiry) > current + 60:
        raise AdmissionError("invalid_admission", 403)
    # /api/curate kept its first message; the other feed routes name themselves, so a proof fits one route only.
    route = "" if path == "/api/curate" else path + ":"
    message = f"ask-curate:v1:{route}{expiry}:{hashlib.sha256(body).hexdigest()}"
    expected = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise AdmissionError("invalid_admission", 403)


def filed(decision: TopicDecision, request: CurateRequest) -> dict:
    """What Ops receives for v3: the topic to publish the question under, and its page's word if asked."""
    keys = {topic.key: topic.label for topic in request.topics}
    if not decision.topic_label.strip():
        raise ValueError("The question needs a topic_label.")
    if decision.topic_key is not None and decision.topic_key not in keys:
        raise ValueError("topic_key must be one of the given keys, or null.")
    if decision.topic_key is None and len(decision.topic_label.strip()) > 16:
        raise ValueError("A new topic_label must be at most 10 Chinese characters.")
    if request.word and not decision.word.strip():
        raise ValueError("The question needs a word for its address.")
    result = {"topic_key": decision.topic_key,
              "topic_label": keys[decision.topic_key] if decision.topic_key else decision.topic_label.strip()}
    # The same words as an answer's own (ops_records.share_word): always a valid address word.
    return {**result, "word": share_word(decision.word)} if request.word else result


async def curate_question(client: httpx.AsyncClient, token: str, model: str, request: CurateRequest) -> dict:
    messages = [
        {"role": "system", "content": TOPIC_PROMPT + (WORD_PROMPT if request.word else "")},
        {"role": "user", "content": json.dumps(request.model_dump(exclude={"word"}), ensure_ascii=False)},
    ]
    deadline = time.monotonic() + CURATE_SECONDS
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure("provider_timeout")
        payload = {"model": model, "temperature": .2, "max_tokens": 400, "messages": messages,
                   "response_format": {"type": "json_object"}, **model_options(model)}
        try:
            async with asyncio.timeout(remaining):
                response = await client.post("https://space.ai-builders.com/backend/v1/chat/completions", json=payload,
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                    timeout=httpx.Timeout(remaining, connect=min(10, remaining), pool=min(5, remaining)), follow_redirects=False)
        except httpx.HTTPError:
            raise ProviderFailure("provider_unavailable") from None
        provider_status(response)
        content = ""
        try:
            choice = (response.json().get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content")
            if choice.get("finish_reason") == "length" or not isinstance(content, str) or len(content) > 40000:
                raise ValueError("The reply was cut off; return the complete JSON object.")
            return filed(TopicDecision.model_validate_json(content), request)
        except (ValueError, ValidationError, TypeError, AttributeError) as exc:
            if attempt:
                raise ProviderFailure("invalid_answer") from None
            # One targeted repair, as for answers.
            messages = [*messages, {"role": "assistant", "content": content if isinstance(content, str) else ""},
                        {"role": "user", "content": "上面的输出不符合要求：" + str(exc)[:600] + "。请只输出修正后的完整 JSON 对象。"}]
    raise ProviderFailure("invalid_answer")
