"""Provider call and server-owned attribution boundary."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .retrieval import Passage
from .answer_contract import model_evidence, system_prompt

ANSWER_BUDGET_SECONDS = 85
DEFAULT_MODEL = "deepseek-v4-flash"
# A backup writes the answer when the answering model has written nothing for FIRST_CONTENT_SECONDS,
# or failed before it wrote anything (2026-10-15, the owner: on that morning, Beijing time,
# DeepSeek through Builder Space stalled for over an hour and five questions ran out of time).
# OpenAI already receives every question to find sources; pages, the app's consent and the
# privacy policy say it also writes the answer then (/api/meta backup_model).
BACKUP_MODELS = {"deepseek-v4-flash": "gpt-5", "deepseek-v4-pro": "gpt-5"}
FIRST_CONTENT_SECONDS = 30
BACKUP_MIN_SECONDS = 20


def model_options(model: str) -> dict:
    """Options accepted by Builder; model selection is server-owned."""
    if model == "gpt-5":
        return {"reasoning_effort": "low"}
    if model == "grok-4.5":
        return {"reasoning_effort": "medium"}
    if model == "deepseek-v4-pro":
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}
    if model == "deepseek-v4-flash":
        return {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}
    return {}

class AnswerSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=90)
    body: str = Field(min_length=1, max_length=2600)
    source_ids: list[str] = Field(max_length=8)
    kind: Literal["synthesis", "application", "source"]


class SourceReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(pattern=r"^S\d{1,2}$")
    reason: str = Field(min_length=1, max_length=150)


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["answered", "clarify", "unsupported"]
    summary: str = Field(min_length=1, max_length=350)
    sections: list[AnswerSection] = Field(max_length=3)
    followups: list[str] = Field(max_length=3)
    clarifying_questions: list[str] = Field(max_length=2)
    limitations: str = Field(default="", max_length=900)
    source_reasons: list[SourceReason] = Field(default_factory=list, max_length=8)
    # The word a shared answer's address carries (ask.lizheng.ai/s/<date>/<slug>); never shown as text.
    slug: str = ""


SYSTEM_PROMPT = system_prompt()


class ProviderFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class InvalidAnswer(ValueError):
    """Fixed validation reason safe to return to the same provider for repair."""


def strict_schema() -> dict:
    schema = ModelAnswer.model_json_schema()
    for node in [schema, *schema.get("$defs", {}).values()]:
        if node.get("type") == "object":
            node["additionalProperties"] = False
            node["required"] = list(node.get("properties", {}))
    return schema


def completed_sections(content: str, passages: list[Passage]) -> list[AnswerSection]:
    """Read complete top-level sections; incomplete JSON and untrusted IDs stay hidden."""
    decoder = json.JSONDecoder()
    cursor = 0
    status = None
    def whitespace(position):
        while position < len(content) and content[position].isspace():
            position += 1
        return position
    cursor = whitespace(cursor)
    if cursor >= len(content) or content[cursor] != "{":
        return []
    cursor += 1
    try:
        while True:
            cursor = whitespace(cursor)
            key, cursor = decoder.raw_decode(content, cursor)
            cursor = whitespace(cursor)
            if not isinstance(key, str) or cursor >= len(content) or content[cursor] != ":":
                return []
            cursor = whitespace(cursor + 1)
            if key == "sections":
                if status != "answered" or cursor >= len(content) or content[cursor] != "[":
                    return []
                cursor += 1
                sections = []
                while len(sections) < 3:
                    cursor = whitespace(cursor)
                    try:
                        value, cursor = decoder.raw_decode(content, cursor)
                        section = AnswerSection.model_validate(value)
                        # Use the same source/quotation/URL boundary as final answers.
                        partial = ModelAnswer(status="answered", summary="回答仍在生成。", sections=[section], followups=[], clarifying_questions=[])
                        normalize_source_bindings(partial, passages)
                        validate_answer(partial, passages)
                        if misattributed(partial, passages):
                            return sections
                    except (ValueError, TypeError):
                        return sections
                    sections.append(section)
                    cursor = whitespace(cursor)
                    if cursor >= len(content) or content[cursor] != ",":
                        return sections
                    cursor += 1
                return sections
            value, cursor = decoder.raw_decode(content, cursor)
            if key == "status":
                status = value
            cursor = whitespace(cursor)
            if cursor >= len(content) or content[cursor] != ",":
                return []
            cursor += 1
    except (ValueError, TypeError):
        return []


def provider_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise ProviderFailure("provider_busy")
    if response.status_code in {401, 403}:
        raise ProviderFailure("model_unavailable")
    if not response.is_success:
        raise ProviderFailure("provider_unavailable")


async def streamed_completion(client, payload, token, remaining, passages, on_progress, on_partial, on_start=None) -> dict | httpx.Response:
    content = ""
    finish = None
    seen_done = False
    sent_sections = 0
    started = False
    async with client.stream(
        "POST", "https://space.ai-builders.com/backend/v1/chat/completions",
        json=payload, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        timeout=httpx.Timeout(remaining, connect=min(10, remaining), pool=min(5, remaining)),
        follow_redirects=False,
    ) as response:
        provider_status(response)
        if "text/event-stream" not in response.headers.get("content-type", ""):
            # A gateway may ignore stream; still validate its complete JSON response.
            await response.aread()
            return response
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                seen_done = True
                break
            if not data or len(data) > 131072:
                raise ProviderFailure("provider_unavailable")
            try:
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise ProviderFailure("provider_unavailable")
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                # Raw reasoning_content is neither stored nor exposed.
                text = delta.get("content")
                if text is not None and not isinstance(text, str):
                    raise ProviderFailure("provider_unavailable")
                if text:
                    content += text
                    if len(content) > 36000:
                        raise InvalidAnswer("输出必须是有界的 JSON 字符串。")
                    if not started:
                        if on_start:
                            on_start()
                        if on_progress:
                            await on_progress({"stage": "drafting", "message": "回答内容已开始返回，正在整理完整段落与出处…"})
                    started = True
                    if on_partial:
                        sections = completed_sections(content, passages)
                        if len(sections) > sent_sections:
                            partial = assemble_answer(ModelAnswer(status="answered", summary="回答仍在生成。", sections=sections, followups=[], clarifying_questions=[]), passages, final=False)
                            await on_partial({"sections": partial["sections"], "sources": partial["sources"]})
                            sent_sections = len(sections)
                finish = choice.get("finish_reason") or finish
            except InvalidAnswer:
                raise
            except (ValueError, TypeError, KeyError, AttributeError):
                raise ProviderFailure("provider_unavailable") from None
    if not seen_done or not content or finish is None:
        raise ProviderFailure("provider_unavailable")
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


class ProviderStalled(Exception):
    """The answering model wrote nothing in time, or failed before it wrote anything."""


async def generate_answer(client: httpx.AsyncClient, token: str, model: str, request: dict, passages: list[Passage],
                          on_progress=None, on_partial=None, backup: str | None = None) -> ModelAnswer:
    """The answer to one question, within one wall-clock budget. With a backup, a model that has
    written nothing after FIRST_CONTENT_SECONDS (or failed first) hands the question to it once."""
    deadline = time.monotonic() + ANSWER_BUDGET_SECONDS
    if not backup or backup == model:
        return await answer_with(client, token, model, request, passages, deadline, on_progress, on_partial)
    try:
        return await answer_with(client, token, model, request, passages, deadline, on_progress, on_partial,
                                 first_content=FIRST_CONTENT_SECONDS)
    except ProviderStalled:
        if deadline - time.monotonic() < BACKUP_MIN_SECONDS:
            raise ProviderFailure("provider_timeout") from None
    if on_progress:
        await on_progress({"stage": "thinking", "message": "回答模型没有及时回应，已换备用模型重新整理回答…"})
    answer = await answer_with(client, token, backup, request, passages, deadline, on_progress, on_partial)
    # No question or answer text in the log: which model stalled and which one answered.
    sys.stderr.write(f"ask: {model} wrote nothing in time; {backup} answered\n")
    sys.stderr.flush()
    return answer


def answer_payload(model: str, request: dict, passages: list[Passage]) -> dict:
    payload = {
        "model": model, "stream": model in {"grok-4.5", "deepseek-v4-flash"}, "temperature": .35, "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({**request, "sources": [model_evidence(passage) for passage in passages]}, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "public_context_answer", "strict": True, "schema": strict_schema()}},
    }
    payload.update(model_options(model))
    if payload["stream"]:
        payload["messages"][0]["content"] += "\n为逐段展示，JSON 顶层字段按 status、summary、sections、followups、clarifying_questions、limitations、source_reasons、slug 的顺序输出；先确定 status，再写 sections。"
    if model in {"deepseek-v4-pro", "deepseek-v4-flash"}:
        # Builder's live endpoint rejects json_schema for this model. Supply
        # the same contract in the prompt and retain all server-side checks.
        payload["response_format"] = {"type": "json_object"}
        payload["messages"][0]["content"] += "\n输出 JSON 必须符合以下 schema（所有字段必填，不得增加字段）：" + json.dumps(strict_schema(), ensure_ascii=False)
    return payload


async def answer_with(client: httpx.AsyncClient, token: str, model: str, request: dict, passages: list[Passage], deadline: float,
                      on_progress=None, on_partial=None, first_content: float | None = None) -> ModelAnswer:
    """One model's answer by the shared deadline. With first_content, a streamed first attempt that
    has written nothing by then, or fails before it writes, raises ProviderStalled."""
    payload = answer_payload(model, request, passages)
    watch = first_content is not None and payload["stream"]
    loop = asyncio.get_running_loop()
    # At most one targeted repair, 8,000 generated tokens in total, and one
    # shared wall-clock budget. Network/auth failures are never blindly retried.
    for attempt in range(2):
        content = ""
        # Watched: the first attempt of a streamed model, until it writes its first words.
        waiting = watch and not attempt
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderFailure("provider_timeout")
            async with asyncio.timeout(min(remaining, first_content) if waiting else remaining) as limit:
                def started():
                    # Writing has begun: the rest of the shared budget applies again.
                    nonlocal waiting
                    if waiting:
                        waiting = False
                        limit.reschedule(loop.time() + max(0.0, deadline - time.monotonic()))
                if payload["stream"]:
                    data = await streamed_completion(client, payload, token, remaining, passages, on_progress, on_partial, on_start=started)
                else:
                    response = await client.post(
                        "https://space.ai-builders.com/backend/v1/chat/completions",
                        json=payload,
                        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                        timeout=httpx.Timeout(remaining, connect=min(10, remaining), pool=min(5, remaining)),
                        follow_redirects=False,
                    )
                    provider_status(response)
                    data = response
            if on_progress:
                await on_progress({"stage": "checking", "message": "回答已生成，正在核对来源编号和输出格式…"})
            try:
                if isinstance(data, httpx.Response):
                    data = data.json()
                choices = data.get("choices") or []
                if not choices or choices[0].get("finish_reason") == "length":
                    raise InvalidAnswer("输出缺失或被截断；请缩短内容并返回完整 JSON。")
                content = choices[0].get("message", {}).get("content")
                if not isinstance(content, str) or len(content) > 36000:
                    content = ""
                    raise InvalidAnswer("输出必须是有界的 JSON 字符串。")
                answer = ModelAnswer.model_validate_json(content)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                if isinstance(exc, InvalidAnswer):
                    raise
                raise InvalidAnswer("输出不是完整、有效且符合 schema 的 JSON；请检查所有字段及数量和长度限制。") from None
            normalize_source_bindings(answer, passages)
            validate_answer(answer, passages)
            # Speaker attribution is an evidence boundary on every attempt.
            if sentences := misattributed(answer, passages):
                if attempt or deadline - time.monotonic() <= REWRITE_SECONDS:
                    raise ProviderFailure("invalid_answer")
                raise InvalidAnswer("有句子将未确认的发言归给了具体人物：" + "；".join(f"「{item[:80]}」" for item in sentences[:2])
                                    + "。节目参与者不等于片段说话人。只将明确归属的发言写成该人的观点；说话人未确认时介绍讨论内容，不指定是谁说的。推荐理由与后续问题也按此处理。其余内容保持不变。")
            return answer
        except InvalidAnswer as exc:
            if not attempt:
                if on_progress:
                    await on_progress({"stage": "repairing", "message": "回答中的来源标注或格式需要修正，正在重新整理…"})
                if content:
                    payload["messages"].append({"role": "assistant", "content": content})
                payload["messages"].append({"role": "user", "content": "上一份输出未通过服务器核对：" + str(exc) + " 请依据同一批证据修复完整 JSON，不生成链接、不编造来源编号、不把发现用元数据当正文依据。不解释修复过程，只返回符合 schema 的对象。"})
                continue
            failure = ProviderFailure("invalid_answer")
        except ProviderFailure as exc:
            if waiting and exc.code != "invalid_answer":
                raise ProviderStalled() from None
            failure = exc
        except (httpx.TimeoutException, TimeoutError):
            if waiting:
                raise ProviderStalled() from None
            failure = ProviderFailure("provider_timeout")
        except httpx.HTTPError:
            if waiting:
                raise ProviderStalled() from None
            failure = ProviderFailure("provider_unavailable")
        raise failure from None
    raise ProviderFailure("invalid_answer")


# A source number as the model writes it. Python's \b sees no edge between a Chinese character and
# a Latin letter, so "可与S1、S2相互印证" once passed every check here and reached readers.
SOURCE_NUMBER = re.compile(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])")
# Readers never see an S number: a mark [S3] becomes the citation 3 in the texts that show marks
# (summary, section bodies, limitations). Anywhere else a number reaches them as a bare label.
MARK = re.compile(r"\[(S\d+)\]")


def normalize_source_bindings(answer: ModelAnswer, passages: list[Passage]) -> None:
    """Bind explicit, known body IDs mechanically; eligibility is still validated below.

    Never infer a missing source from content, add an unknown ID, or silently truncate
    a section's evidence. The same normalization runs before partial and final checks.
    """
    known = {p.source["id"] for p in passages}
    for section in answer.sections:
        explicit = SOURCE_NUMBER.findall(section.heading + "\n" + section.body)
        combined = list(dict.fromkeys([*section.source_ids, *(i for i in explicit if i in known)]))
        if len(combined) <= 8:
            section.source_ids = combined


def validate_answer(answer: ModelAnswer, passages: list[Passage]) -> None:
    by_id = {passage.source["id"]: passage for passage in passages}
    titles = {passage.source["title"].strip() for passage in passages}
    for text in [answer.summary, answer.limitations, *answer.followups, *answer.clarifying_questions,
                 *(item.reason for item in answer.source_reasons),
                 *(section.heading + "\n" + section.body for section in answer.sections)]:
        if re.search(r"https?://|www\.|\]\s*\(|^\s*>", text, flags=re.I | re.M):
            raise InvalidAnswer("正文不能生成 URL、超链接或块引用；使用来源编号并转述内容。")
        # Quoting a title or marking a concept is ordinary punctuation. Reject
        # claimed verbatim speech, rather than every long pair of quote marks.
        for quoted in re.finditer(r'["“「]([^"”」\n]{18,})["”」]', text):
            prefix = text[max(0, quoted.start() - 22):quoted.start()]
            if quoted.group(1).strip() not in titles and re.search(r"原话|逐字|写道|引用|名言|他说|她说|立正说|作者说|文中说|说过|提到|指出|表示|认为|一句话", prefix):
                raise InvalidAnswer("不能把模型生成的句子标为作者原话；改为有来源的转述。")
        if any(source_id not in by_id for source_id in SOURCE_NUMBER.findall(text)):
            raise InvalidAnswer("文字引用了不存在的来源编号；只能使用提供的 S 编号。")
    if any(item.source_id not in by_id for item in answer.source_reasons):
        raise InvalidAnswer("来源推荐理由引用了不存在的编号；只能使用提供的 S 编号。")
    for section in answer.sections:
        if not section.source_ids or any(source_id not in by_id for source_id in section.source_ids):
            raise InvalidAnswer("每个 section 必须引用提供的有效 source_ids；无证据的部分应使用 clarify 或 unsupported。")
        if any(by_id[source_id].discovery for source_id in section.source_ids):
            raise InvalidAnswer("发现用元数据不包含正文，不能支撑 section；请选择有正文证据的来源。")
        if not set(SOURCE_NUMBER.findall(section.heading + "\n" + section.body)).issubset(section.source_ids):
            raise InvalidAnswer("正文中的来源编号也必须放在该 section.source_ids 中。")
    if answer.status == "answered" and not answer.sections:
        raise InvalidAnswer("answered 必须有至少一个有依据的 section；资料不足应使用 clarify 或 unsupported。")
    if answer.status == "unsupported" and answer.clarifying_questions:
        raise InvalidAnswer("资料范围外的问题应说明不足，unsupported 的 clarifying_questions 必须为空。")
    if any(len(question) > 250 or not question.strip() for question in answer.followups + answer.clarifying_questions):
        raise InvalidAnswer("后续问题必须非空且每个不超过 250 字。")


# Sources that carry 立正's own view: his published writing and his solo talks.
OWN_VIEW_ORIGINS = frozenset({"yuzheng-published-text", "yuzheng-spoken-source"})
# With less time left, source-number wording can fall back; speaker misattribution cannot.
REWRITE_SECONDS = 40


def host_names(passages: list[Passage]) -> list[str]:
    """The host's names from the shared name table, longest first; 立正 before a table existed."""
    names = next((passage.evidence.get("host_names") for passage in passages if passage.evidence.get("host_names")), None)
    return sorted(set(names or ["立正"]), key=len, reverse=True)


def attribution_fields(answer: ModelAnswer, passages: list[Passage]) -> list[tuple[str, list[str]]]:
    """Every visible field uses the same source bindings; semantic entailment stays an eval task."""
    all_ids = [p.source["id"] for p in passages]
    return [(answer.summary, all_ids), (answer.limitations, all_ids),
            *[(s.heading, s.source_ids) for s in answer.sections],
            *[(s.body, s.source_ids) for s in answer.sections],
            *[(r.reason, [r.source_id]) for r in answer.source_reasons],
            *[(q, all_ids) for q in answer.followups + answer.clarifying_questions]]


def misattributed(answer: ModelAnswer, passages: list[Passage]) -> list[str]:
    """Reject named claims without a corresponding author or fragment speaker."""
    evidence = {passage.source["id"]: passage.evidence for passage in passages}
    non_stance_acts = {"question", "introduction", "acknowledgement", "joke", "reported-other"}
    host = "|".join(re.escape(name) for name in host_names(passages))
    view_claim = re.compile(r"(?:" + host + r")(?:本人)?(?:认为|主张|觉得|相信|建议|赞同|支持|反对|强调|说(?:过|道)?|提到|指出|表示|写道|的(?:立场|观点|判断))", re.I)
    names_host = re.compile(r"(?<![A-Za-z0-9])(?:" + host + r")(?![A-Za-z0-9])", re.I)
    found = []
    fields = attribution_fields(answer, passages)
    for text, available in fields:
        for sentence in re.findall(r"[^。！？]+[。！？]?(?:\s*\[S\d+\])*", text):
            explicit = SOURCE_NUMBER.findall(sentence)
            cited = explicit or available
            if not cited or not names_host.search(sentence.replace("问问立正", "")):
                continue
            claim = view_claim.search(sentence)
            # A byline or publisher description names a person without assigning
            # a view. Citation presence must not turn that description into one.
            if not claim:
                continue
            denies_inference = claim and re.search(r"不能|无法|不足以|没有证据|尚不能", sentence[:claim.start()])
            if denies_inference:
                continue
            own = [evidence[c] for c in cited if evidence.get(c, {}).get("content_origin") in OWN_VIEW_ORIGINS]
            if not own:
                found.append(sentence.strip())
                continue
            # A question carrying his speaker ID is not stance evidence.
            supports_view = any(item.get("yuzheng_stance_weight") != "not-evidence"
                                and item.get("speech_act") not in non_stance_acts for item in own)
            if claim and not denies_inference and not supports_view:
                found.append(sentence.strip())
    # Programme names support discovery; only a fragment's speaker binding supports
    # a named guest's claim. Apply the same boundary to recommendations and followups.
    by_id = {p.source["id"]: p for p in passages}
    # Each name readers may use for a participant, with the entities it can mean.
    aliases: dict[str, set[str]] = {}
    for p in passages:
        for entity_id, names in (p.evidence.get("participant_names_by_id") or {}).items():
            for name in names:
                aliases.setdefault(name.strip(), set()).add(entity_id)
        for key in ("participant_names", "participant_aliases"):
            for name in str(p.evidence.get(key) or p.source.get(key) or "").split("；"):
                if name.strip():
                    aliases.setdefault(name.strip(), set())
    predicates = r"(?:本人)?(?:(?:在|对)[^。！？]{0,28}?)?(?:说(?:过|的|道)?|认为|主张|觉得|相信|建议|赞同|支持|反对|强调|指出|提到|表示|回答|给(?:了|出)|下定义|的(?:观点|立场|判断))"
    for text, available in fields:
        for sentence in re.findall(r"[^。！？]+[。！？]?(?:\s*\[S\d+\])*", text):
            ids = SOURCE_NUMBER.findall(sentence) or available
            relevant = [by_id[i] for i in ids if i in by_id]
            for name, entities in aliases.items():
                claim = re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"\s*" + predicates, sentence, re.I)
                if not claim or re.search(r"不能|无法|不足以|没有证据|尚不能", sentence[:claim.start()]):
                    continue
                # The fragment's speaker ID decides; a recorded name ("卢蔚（Cooper）") need not match the wording.
                confirmed = any(p.source.get("speaker_id") not in (None, "", "unknown")
                                and p.source.get("speaker_status") != "unresolved"
                                and p.evidence.get("speech_act") not in non_stance_acts
                                and (p.source.get("speaker_id") in entities
                                     or name.casefold() == str(p.source.get("speaker_name") or "").casefold())
                                for p in relevant)
                if not confirmed and sentence.strip() not in found:
                    found.append(sentence.strip())
    return found


def answer_fields(answer: ModelAnswer) -> list[tuple[str, bool]]:
    """Every text of an answer a reader sees, and whether it shows [S3] as a citation mark."""
    return [(answer.summary, True), (answer.limitations, True), *((section.body, True) for section in answer.sections),
            *((section.heading, False) for section in answer.sections), *((item, False) for item in answer.followups),
            *((item, False) for item in answer.clarifying_questions), *((item.reason, False) for item in answer.source_reasons)]


def numbers_as_names(fields: list[tuple[str, bool]]) -> list[str]:
    """Sentences where a reader would meet a source number as a label: named in a sentence
    ("S1 说得更直接", "可与S1、S2相互印证"), or anywhere in a text that shows no marks."""
    return [sentence.strip() for text, marks in fields for sentence in re.findall(r"[^。！？\n]+[。！？]?", text)
            if SOURCE_NUMBER.search(MARK.sub("", sentence) if marks else sentence)]


def short_title(title: str) -> str:
    """A source's title as a sentence can carry it, in 《》: a book's own 《》 title, otherwise the
    title without a leading label, series name or subtitle."""
    title = " ".join(title.split())
    if book := re.search(r"《[^》]+》", title):
        return book.group(0)
    title = re.sub(r"^【[^】]*】|^[^·｜|]{1,12} · ", "", title).strip() or title
    main = re.split(r"\s*[｜|]\s*", title)[0] or title
    if len(main) > 24:
        main = re.split(r"[：:]", main)[0] or main
    if len(main) > 24:
        cut = max(main.rfind(mark, 0, 24) for mark in "，、；")
        main = main[:cut] if cut >= 8 else main[:24] + "…"
    return f"《{main}》"


# Numbers the model put in brackets instead of marks: [S1, S2], （S1、S2）, 【S1】. A mark [S1] is one too.
NUMBER_GROUP = re.compile(r"([\[［【（(])\s*(S\d+(?:\s*[,，、;；/和与及]\s*S\d+)*)\s*[\]］】）)]")
# A mark, which stays, or a number written as a name, with the spaces around it.
NAMED_NUMBER = re.compile(r"\[S\d+\]|[ \t]*(?<![A-Za-z0-9])(S\d+)(?![A-Za-z0-9])[ \t]*")
# A mark the model wrote without its letter: [3] for [S3]. Pages show it as it is.
LETTERLESS_MARK = re.compile(r"[\[［【](\d{1,2})[\]］】]")


def unlabel(text: str, titles: dict[str, str], cite: set[str] | None) -> str:
    """The words a reader can follow where a text names sources by number. titles holds what to
    write for each known number; cite holds the numbers this text may mark, or is None where marks
    do not show. A mark without its letter gets it back; a named source becomes its title, marked
    where it may be; numbers in brackets become marks, or where marks do not show, go at the end
    of a sentence and become titles inside it; an unknown number goes. Text without such numbers
    is returned as it is."""
    text = LETTERLESS_MARK.sub(lambda match: f"[S{match.group(1)}]" if f"S{match.group(1)}" in titles else match.group(0), text)
    if not numbers_as_names([(text, cite is not None)]):
        return text
    def group(match):
        ids = SOURCE_NUMBER.findall(match.group(2))
        if cite is not None:
            return "".join(f"[{source_id}]" for source_id in ids if source_id in cite)
        named = [titles[source_id] for source_id in ids if titles.get(source_id)]
        if not named:
            return ""
        if match.group(1) in "（(":
            return f"（{'、'.join(named)}）"
        # A bracket the sentence goes on from names its sources; one that ends it was a citation.
        return "、".join(named) if match.string[match.end():match.end() + 1].isalnum() else ""
    def name(match):
        if match.group(1) is None:
            return match.group(0)
        source_id = match.group(1)
        mark = f"[{source_id}]" if cite is not None and source_id in cite else ""
        word = titles[source_id] + mark if source_id in titles else ""
        # Chinese needs no space beside a title; Latin text keeps one.
        around = match.string[match.start() - 1:match.start()], match.string[match.end():match.end() + 1]
        latin = [char.isascii() and char.isalnum() for char in around]
        if not word:
            return " " if all(latin) else ""
        return (" " if latin[0] else "") + word + (" " if latin[1] else "")
    text = NAMED_NUMBER.sub(name, NUMBER_GROUP.sub(group, text))
    return re.sub(r"[ \t]+(?=[，。；：！？、）])", "", text).strip()


def unlabeled(text: str, titles: dict[str, str], limit: int, cite: set[str] | None = None) -> str:
    """unlabel within the length the archive accepts for this field: a title is longer than the
    number it replaces, so past the limit the number goes and only its mark stays."""
    for names in (titles, dict.fromkeys(titles, "")):
        if len(value := unlabel(text, names, cite)) <= limit:
            return value or text
    return text


def cite_before_stop(text: str) -> str:
    """Keep citations with the sentence they support: in “…上。[S3] 立正…” the number reads as
    the next sentence's, so it moves before the full stop. A paragraph break after the marks stays
    after the stop, which would otherwise open the next paragraph."""
    return re.sub(r"([。！？；])\s*((?:\[S\d+\][ \t]*)+)", lambda match: match.group(2).replace(" ", "").replace("\t", "") + match.group(1), text)


def renumbered(result: dict) -> dict:
    """Sources numbered 1 to n in the order a reader meets them, as the saved image and PDF number
    them. The model's numbers follow retrieval, so the ones an answer uses leave gaps (1, 2, 3, 8)."""
    met = [*MARK.findall(result["summary"]),
           *(source_id for section in result["sections"] for source_id in [*MARK.findall(section["body"]), *section["source_ids"]]),
           *MARK.findall(result["limitations"])]
    first = {source_id: index for index, source_id in enumerate(dict.fromkeys(met))}
    sources = sorted(result["sources"], key=lambda source: first.get(source["id"], len(first)))
    number = {source["id"]: f"S{index + 1}" for index, source in enumerate(sources)}
    def marks(text):
        return MARK.sub(lambda match: f"[{number[match.group(1)]}]" if match.group(1) in number else "", text)
    return {**result, "summary": marks(result["summary"]), "limitations": marks(result["limitations"]),
            "sections": [{**section, "body": marks(section["body"]),
                          "source_ids": [number[source_id] for source_id in section["source_ids"] if source_id in number]}
                         for section in result["sections"]],
            "sources": [{**source, "id": number[source["id"]]} for source in sources]}


def assemble_answer(answer: ModelAnswer, passages: list[Passage], final: bool = True) -> dict:
    """The answer as readers get it: server-owned sources, and no source number except as a mark.
    A partial answer keeps the model's numbers, since pages match its sources by number to the
    candidates already shown; the final one is numbered 1 to n."""
    titles = {passage.source["id"]: short_title(passage.source["title"]) for passage in passages}
    known = set(titles)
    # The limits are the model's own (ModelAnswer, AnswerSection, validate_answer), which the archive checks again.
    answer = answer.model_copy(update={
        "summary": cite_before_stop(unlabeled(answer.summary, titles, 350, known)),
        "limitations": cite_before_stop(unlabeled(answer.limitations, titles, 900, known)),
        "sections": [section.model_copy(update={"heading": unlabeled(section.heading, titles, 90),
                                                "body": cite_before_stop(unlabeled(section.body, titles, 2600, set(section.source_ids)))})
                     for section in answer.sections],
        "followups": [unlabeled(item, titles, 250) for item in answer.followups],
        "clarifying_questions": [unlabeled(item, titles, 250) for item in answer.clarifying_questions]})
    used = set(source_id for section in answer.sections for source_id in section.source_ids)
    used.update(MARK.findall(answer.summary + " " + answer.limitations))
    reasons = {item.source_id: unlabeled(item.reason, titles, 150) for item in answer.source_reasons}
    def source_with_reason(passage):
        return {**passage.source, "reason": reasons.get(passage.source["id"], "可回到原文核对这部分判断")}
    selected = [source_with_reason(passage) for passage in passages if passage.source["id"] in used]
    if not selected and answer.status != "unsupported":
        selected = [source_with_reason(passage) for passage in passages[:5]]
    result = {**answer.model_dump(exclude={"source_reasons", "slug"}), "sources": selected}
    return renumbered(result) if final else result


def sources_only(passages: list[Passage], reason: str = "", intent: str = "understand") -> dict:
    return {
        "status": "sources-only",
        "summary": "找到了这些可能有帮助的公开材料。" if passages else "暂时没有找到足以回应这个问题的公开材料。",
        "sections": [], "sources": [passage.source for passage in passages[:8]],
        "followups": [], "clarifying_questions": [], "limitations": reason,
    }


def guest_reading_result(passages: list[Passage], people: list[str] | None = None) -> dict:
    """Named-person reading requests are source navigation with exact attribution."""
    selected, seen = [], set()
    for passage in passages:
        family = passage.evidence.get("source_family") or passage.source["url"].split("&t=")[0]
        if family not in seen:
            selected.append(passage); seen.add(family)
    result = sources_only(selected, intent="find")
    if selected:
        people = [name for name in dict.fromkeys(people or []) if name]
        if len(people) > 1:
            # Namesakes or several people: say who, rather than mixing them silently.
            result["summary"] = f"这个名字对应{len(people)}位嘉宾：{'、'.join(people)}。下面是他们的节目与原文片段。"
        elif people:
            result["summary"] = f"找到了{people[0]}的节目与原文片段。"
        else:
            result["summary"] = "找到了这些相关节目与原文片段。"
    result["sources"] = [{**s, "id": "S" + str(i)} for i, s in enumerate(result["sources"], 1)]
    return result


def attribution_clarification(question: str, context: str = "", history: list | None = None) -> dict | None:
    # Unnamed people's statements cannot be established using a different
    # speaker's retrieved excerpt. Ask for the source before making any claim.
    if context.strip() or history or not re.search(r"某位嘉宾|某个嘉宾|那位嘉宾|那个嘉宾|某位受访者", question):
        return None
    if not re.search(r"说|观点|判断|建议|保证|承诺|认为|谈到|提到|表示|预测", question):
        return None
    return {"status": "clarify", "summary": "需要先确认你指的是哪位嘉宾或哪一期节目，才能核对他的具体说法。",
            "sections": [], "sources": [], "followups": [],
            "clarifying_questions": ["你指的是哪位嘉宾，或哪一期节目？"],
            "limitations": "嘉宾的观点需要对应具体说话人和材料；其他人的片段不能替他作答。"}


def unsupported_result(passages: list[Passage]) -> dict:
    return {
        "status": "unsupported",
        "summary": "当前公开材料不足以回答这个问题。",
        "sections": [], "sources": [passage.source for passage in passages[:4]],
        "followups": [], "clarifying_questions": [],
        "limitations": "仅收录标题或嘉宾信息的资料可以帮助找内容，不能用来推断其中的观点。" if passages else "可以补充一个主题、关键词或具体处境，再试一次。",
    }
