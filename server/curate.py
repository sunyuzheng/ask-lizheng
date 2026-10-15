"""Ops' automatic feed: whether an asked question is published, and under which topic.

Ops sends one question asked under the v4 notice (no situation, first turn) with its archived
answer and the topics already published. The model decides whether others would find it worth
reading and names its topic; the question and answer are published as asked (2026-10-05, the user:
「自动去个人信息这一步删掉吧，没必要了」; the asker was told it is like raising a hand at a talk,
public with no names). Ops publishes what comes back; the owner can withdraw any item there.

Until 2026-10-14 this also wrote de-identified public versions (v1, for questions asked before that
wording) and generic questions for a first batch of themes (/api/themes, /api/seed-answer). Ops
withdrew both on 2026-10-14 and no longer asks; they went from here on 2026-10-15.
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

CURATE_HEADER = "x-ask-curate-proof"
CURATE_BODY_LIMIT = 262144
CURATE_SECONDS = 75
SOURCE_ID = r"^S\d{1,2}$"
SKIP_REASONS = ("personal", "sensitive", "low_quality", "weak_answer", "unsafe")


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
    v: Literal[2]
    question: str = Field(min_length=1, max_length=2000)
    answer: CurateAnswer
    topics: list[CurateTopic] = Field(default_factory=list, max_length=300)


class JudgeDecision(BaseModel):
    """v2: whether to publish as asked, and the topic. When it declines, only skip_reason matters."""
    model_config = ConfigDict(extra="forbid")
    publish: bool
    skip_reason: Literal["personal", "sensitive", "low_quality", "weak_answer", "unsafe"] | None
    topic_key: str | None
    topic_label: str = Field(max_length=80)


JUDGE_PROMPT = """你在为「问问立正」挑选公开问答。提问的人提交前已经被告知：这里像在讲座上举手提问，问答会公开，但不记名，只写愿意当众说的话。问题和回答会原样公开，你不改写。
你会收到一个问题、它的回答（按段落，带出处编号S1、S2…）和已经公开的主题列表。请做两件事：

1. 判断要不要公开（publish）。值得公开的：别人也可能想问的问题，并且回答有实质内容、能帮到人。不公开时给出 skip_reason：
   - personal：写了能认出具体某个人的信息（私人的名字、联系方式、账号，或者公司、学校加职位这类能对上某个人的组合；立正、公开人物和公开作品不算），或者问题只关于提问者自己的具体处境，别人不会这样问；
   - sensitive：涉及健康、法律纠纷、感情、具体的财务情况，或者别人的隐私；
   - low_quality：测试、乱码、过短或没有意义的问题；
   - weak_answer：回答没有实质内容，或没有回答这个问题；
   - unsafe：违法、有害或不适合公开的内容。
2. 要公开时归到主题。已有主题如果和这个问题问的是同一件事，topic_key 填那个 key、topic_label 填它的名称；否则 topic_key 填 null，起一个新的主题名，不超过10个字，说清问的是什么，例如「学会还是看懂」「AI提效与工作价值」。

只输出一个 JSON 对象，字段都必须有：{"publish": true或false, "skip_reason": 上面五个之一或null, "topic_key": 已有key或null, "topic_label": "主题名"}。
不公开时，publish 为 false、给出 skip_reason，topic_key 为 null，topic_label 为空字符串。"""


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


def judged(decision: JudgeDecision, request: CurateRequest) -> dict:
    """What Ops receives for v2: a decline with its reason, or the topic to publish the question under."""
    if not decision.publish:
        if decision.skip_reason is None:
            raise ValueError("A declined question needs skip_reason.")
        return {"publish": False, "skip_reason": decision.skip_reason}
    keys = {topic.key: topic.label for topic in request.topics}
    if decision.skip_reason is not None or not decision.topic_label.strip():
        raise ValueError("A published question needs a topic and no skip_reason.")
    if decision.topic_key is not None and decision.topic_key not in keys:
        raise ValueError("topic_key must be one of the given keys, or null.")
    if decision.topic_key is None and len(decision.topic_label.strip()) > 16:
        raise ValueError("A new topic_label must be at most 10 Chinese characters.")
    return {"publish": True, "topic_key": decision.topic_key,
            "topic_label": keys[decision.topic_key] if decision.topic_key else decision.topic_label.strip()}


async def curate_question(client: httpx.AsyncClient, token: str, model: str, request: CurateRequest) -> dict:
    messages = [
        {"role": "system", "content": JUDGE_PROMPT},
        {"role": "user", "content": json.dumps(request.model_dump(), ensure_ascii=False)},
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
            return judged(JudgeDecision.model_validate_json(content), request)
        except (ValueError, ValidationError, TypeError, AttributeError) as exc:
            if attempt:
                raise ProviderFailure("invalid_answer") from None
            # One targeted repair, as for answers.
            messages = [*messages, {"role": "assistant", "content": content if isinstance(content, str) else ""},
                        {"role": "user", "content": "上面的输出不符合要求：" + str(exc)[:600] + "。请只输出修正后的完整 JSON 对象。"}]
    raise ProviderFailure("invalid_answer")
