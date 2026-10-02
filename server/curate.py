"""Public versions of asked questions, written by the model for Ops' automatic feed.

Ops sends one question asked under the v4 notice (no situation, first turn) with its archived
answer and the topics already published. The model decides whether others would find it worth
reading, removes anything that could identify the asker or anyone else, and names its topic. Ops
publishes what comes back; the owner can withdraw any item there.
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
    v: Literal[1]
    question: str = Field(min_length=1, max_length=2000)
    answer: CurateAnswer
    topics: list[CurateTopic] = Field(default_factory=list, max_length=300)


class PublicSection(BaseModel):
    """Within the limits Ops checks for every archived or public answer."""
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=90)
    body: str = Field(min_length=1, max_length=2600)
    source_ids: list[str] = Field(max_length=8)
    kind: Literal["source", "synthesis", "application"]


class SourceReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=SOURCE_ID)
    reason: str = Field(max_length=300)


class CurateDecision(BaseModel):
    """The model's whole reply. When it declines, only skip_reason matters."""
    model_config = ConfigDict(extra="forbid")
    publish: bool
    skip_reason: Literal["personal", "sensitive", "low_quality", "weak_answer", "unsafe"] | None
    topic_key: str | None
    topic_label: str = Field(max_length=80)
    question: str = Field(max_length=300)
    summary: str = Field(max_length=350)
    sections: list[PublicSection] = Field(max_length=3)
    limitations: str = Field(max_length=900)
    followups: list[str] = Field(max_length=3)
    source_reasons: list[SourceReason] = Field(max_length=12)


PROMPT = """你在为「问问立正」整理公开问答。提问的人在提交前已经被告知：问答去掉个人信息后可能公开，帮到更多人。
你会收到一个问题、它的回答（按段落，带出处编号S1、S2…）和已经公开的主题列表。请做三件事：

1. 判断要不要公开（publish）。值得公开的：别人也可能想问的问题，并且回答有实质内容、能帮到人。不公开时给出 skip_reason：
   - personal：问题离不开提问者自己的经历和处境，去掉个人信息后就不成立；
   - sensitive：涉及健康、法律纠纷、感情、财务细节或他人隐私；
   - low_quality：测试、乱码、过短或没有意义的问题；
   - weak_answer：回答没有实质内容，或没有回答这个问题；
   - unsafe：违法、有害或不适合公开的内容。
2. 要公开时，去掉一切可能认出提问者或其他人的信息：人名、公司、学校、机构、具体城市、职位与团队细节、收入年龄日期金额等精确数字、独特的经历。把问题改写成别人也会问的说法，保留原意和语气，不要加入原文没有的内容，不超过100字。回答同样处理：保留结构、判断和出处编号，只删改涉及个人的细节；不要新增观点。立正的名字、立正公开的作品和公开人物不算个人信息。
3. 归到主题。已有主题如果和这个问题问的是同一件事，topic_key 填那个 key、topic_label 填它的名称；否则 topic_key 填 null，起一个新的主题名，不超过10个字，说清问的是什么，例如「学会还是看懂」「AI提效与工作价值」。

只输出一个 JSON 对象，字段都必须有：
{"publish": true或false, "skip_reason": 上面五个之一或null, "topic_key": 已有key或null, "topic_label": "主题名",
 "question": "公开版问题", "summary": "公开版回答摘要", "sections": [{"heading": "", "body": "", "source_ids": ["S1"], "kind": "source|synthesis|application"}],
 "limitations": "回答的边界，可为空字符串", "followups": ["可以接着问的问题，最多3个"], "source_reasons": [{"id": "S1", "reason": "这份出处和问题的关系，不含个人信息"}]}
不公开时，publish 为 false、给出 skip_reason，其余文字字段填空字符串、列表填 []。
summary 不超过300字；段落最多3段，每段标题不超过40字；source_ids 和 source_reasons 只能用回答里出现过的出处编号，reason 不超过100字。"""


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


def checked(decision: CurateDecision, request: CurateRequest) -> dict:
    """What Ops receives: a decline with its reason, or a complete public version."""
    if not decision.publish:
        if decision.skip_reason is None:
            raise ValueError("A declined question needs skip_reason.")
        return {"publish": False, "skip_reason": decision.skip_reason}
    sources = {source.id for source in request.answer.sources}
    keys = {topic.key: topic.label for topic in request.topics}
    if (decision.skip_reason is not None or not decision.topic_label.strip() or not decision.question.strip()
            or not decision.summary.strip() or not decision.sections):
        raise ValueError("A published question needs a topic, question, summary and at least one section.")
    if decision.topic_key is not None and decision.topic_key not in keys:
        raise ValueError("topic_key must be one of the given keys, or null.")
    if decision.topic_key is None and len(decision.topic_label.strip()) > 16:
        raise ValueError("A new topic_label must be at most 10 Chinese characters.")
    used = {source_id for section in decision.sections for source_id in section.source_ids}
    if not used <= sources or not {item.id for item in decision.source_reasons} <= sources:
        raise ValueError("Only the answer's own source ids may be cited.")
    if any(len(item.strip()) == 0 or len(item) > 200 for item in decision.followups):
        raise ValueError("Each followup must be a short question.")
    return {
        "publish": True,
        "topic_key": decision.topic_key,
        "topic_label": keys[decision.topic_key] if decision.topic_key else decision.topic_label.strip(),
        "question": decision.question.strip(),
        "summary": decision.summary.strip(),
        "sections": [section.model_dump() for section in decision.sections],
        "limitations": decision.limitations.strip(),
        "followups": [item.strip() for item in decision.followups],
        "source_reasons": [item.model_dump() for item in decision.source_reasons if item.id in used],
    }


async def curate_question(client: httpx.AsyncClient, token: str, model: str, request: CurateRequest) -> dict:
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(request.model_dump(), ensure_ascii=False)},
    ]
    deadline = time.monotonic() + CURATE_SECONDS
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure("provider_timeout")
        payload = {"model": model, "temperature": .2, "max_tokens": 3500, "messages": messages,
                   "response_format": {"type": "json_object"}, **model_options(model)}
        async with asyncio.timeout(remaining):
            response = await client.post("https://space.ai-builders.com/backend/v1/chat/completions", json=payload,
                headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                timeout=httpx.Timeout(remaining, connect=min(10, remaining), pool=min(5, remaining)), follow_redirects=False)
        provider_status(response)
        content = ""
        try:
            choice = (response.json().get("choices") or [{}])[0]
            content = choice.get("message", {}).get("content")
            if choice.get("finish_reason") == "length" or not isinstance(content, str) or len(content) > 40000:
                raise ValueError("The reply was cut off; return the complete JSON object.")
            return checked(CurateDecision.model_validate_json(content), request)
        except (ValueError, ValidationError, TypeError, AttributeError) as exc:
            if attempt:
                raise ProviderFailure("invalid_answer") from None
            # One targeted repair, as for answers.
            messages = [*messages, {"role": "assistant", "content": content if isinstance(content, str) else ""},
                        {"role": "user", "content": "上面的输出不符合要求：" + str(exc)[:600] + "。请只输出修正后的完整 JSON 对象。"}]
    raise ProviderFailure("invalid_answer")


# The first batch: themes drawn from questions asked before v4, which may never be shown themselves.
# Ops sends their text here once; only generic questions, written fresh, come back.
THEMES_PROMPT = """下面是「问问立正」此前收到的提问，只供你归纳主题；这些提问不能公开，你的输出会公开。
请找出被问得最多的共性主题，最多30个，每个主题至少有2条提问属于它。对每个主题：
- topic_label：不超过10个字的主题名，说清问的是什么，例如「学会还是看懂」「AI提效与工作价值」；
- question：用你自己的话写一个通用的问题，不超过60字，任何人都可能这样问。不能照抄或接近照抄任何一条提问，不能带任何个人信息、公司、人名、城市、具体数字或个人经历；
- count：属于这个主题的提问条数。
跳过健康、法律纠纷、感情、财务等隐私话题，跳过测试、乱码和没有意义的提问。
只输出一个 JSON 对象：{"themes": [{"topic_label": "", "question": "", "count": 2}]}，按 count 从大到小。"""


class ThemesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1]
    questions: list[str] = Field(min_length=1, max_length=300)


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_label: str = Field(min_length=1, max_length=16)
    question: str = Field(min_length=4, max_length=120)
    count: int = Field(ge=1, le=1000)


class ThemesReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    themes: list[Theme] = Field(max_length=40)


def borrowed(question: str, asked: list[str]) -> bool:
    """True when a generic question repeats an asker's wording: a run of 12 or more characters, or nearly the whole."""
    import difflib
    plain = re.sub(r"[\s，。？！、：；,.?!:;「」“”\"'（）()]", "", question)
    for item in asked:
        other = re.sub(r"[\s，。？！、：；,.?!:;「」“”\"'（）()]", "", item)
        match = difflib.SequenceMatcher(None, plain, other, autojunk=False)
        if match.find_longest_match(0, len(plain), 0, len(other)).size >= 12 or match.ratio() > .8:
            return True
    return False


async def generic_themes(client: httpx.AsyncClient, token: str, model: str, request: ThemesRequest) -> list[dict]:
    asked = [item[:2000] for item in request.questions if item.strip()]
    payload = {"model": model, "temperature": .2, "max_tokens": 4000, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": THEMES_PROMPT},
                            {"role": "user", "content": json.dumps({"questions": asked}, ensure_ascii=False)}], **model_options(model)}
    async with asyncio.timeout(CURATE_SECONDS):
        response = await client.post("https://space.ai-builders.com/backend/v1/chat/completions", json=payload,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            timeout=httpx.Timeout(CURATE_SECONDS, connect=10, pool=5), follow_redirects=False)
    provider_status(response)
    try:
        choice = (response.json().get("choices") or [{}])[0]
        content = choice.get("message", {}).get("content")
        if choice.get("finish_reason") == "length" or not isinstance(content, str):
            raise ValueError()
        themes = ThemesReply.model_validate_json(content).themes
    except (ValueError, ValidationError, TypeError, AttributeError):
        raise ProviderFailure("invalid_answer") from None
    out, labels = [], set()
    for theme in sorted(themes, key=lambda item: -item.count):
        label, question = theme.topic_label.strip(), theme.question.strip()
        # Fresh wording only, and one question per topic.
        if theme.count < 2 or label in labels or len(label) > 10 or borrowed(question, asked):
            continue
        labels.add(label)
        out.append({"topic_label": label, "question": question, "count": theme.count})
    return out


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1]
    question: str = Field(min_length=4, max_length=120)
