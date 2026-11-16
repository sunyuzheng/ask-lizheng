"""Semantic selection is a bounded retrieval default, never a source-ownership boundary."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
import httpx

SELECTION_SECONDS = 8
URL = "https://space.ai-builders.com/backend/v1/chat/completions"


def empty_answer():
    from .answers import ModelAnswer
    return ModelAnswer(status="unsupported", summary="当前候选片段不足以直接支持这个问题；这不表示作者从未谈过。",
                       sections=[], followups=[], clarifying_questions=[], slug="source-gap")


@dataclass
class EvidenceSelection:
    passages: list
    search_query: str = ""


async def plan_evidence(client, token, model, request, passages, *, allow_supplement=False):
    from .answer_contract import CRITERIA, model_evidence
    from .answers import model_options
    # Keep the accepted stable path byte-identical. The pilot did not establish
    # a recall gain; missing-evidence search is an explicit development option.
    extra = ("\n逐项检查问题涉及的对象、比较项、适用条件，以及读者记得作者说过的话；不要只留下主话题而丢掉决定答案的限制或反例。"
             "\n只能选给出的编号，不补写事实；没有相关正文可以为空。若缺少关键一项，可提出一次简短的公开资料检索，不写用户背景、完整问题或历史。检索结果仍须读原文，不能把检索词当事实。"
             "\n只输出JSON：{\"source_ids\":[\"S1\"],\"search_query\":\"\"}。不需要补搜时search_query为空。"
             if allow_supplement else "\n只能选给出的编号，不补写事实；没有相关正文可以为空。只输出JSON：{\"source_ids\":[\"S1\"]}。")
    payload = {
        "model": model, "temperature": 0, "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "为有来源的问答挑选证据。保留能直接支撑回答、关键条件或必要分歧的excerpt，不为了凑数量保留旁枝。遵守同一回答合同：\n"
                + "\n".join(CRITERIA[k]["instruction"] for k in ("relevance", "faithfulness", "attribution"))
                + extra},
            {"role": "user", "content": json.dumps({
                **{k: request.get(k, [] if k == "history" else "") for k in ("question", "context", "history")},
                "sources": [model_evidence(p) for p in passages]}, ensure_ascii=False)},
        ], **model_options(model),
    }
    try:
        async with asyncio.timeout(SELECTION_SECONDS):
            response = await client.post(URL, json=payload, headers={"Authorization": "Bearer " + token},
                                         timeout=SELECTION_SECONDS, follow_redirects=False)
            response.raise_for_status()
            value = json.loads(response.json()["choices"][0]["message"]["content"])
            ids = value["source_ids"]
            known = {p.source["id"] for p in passages}
            query = value.get("search_query", "")
            allowed = {"source_ids", "search_query"} if allow_supplement else {"source_ids"}
            if (not {"source_ids"} <= set(value) <= allowed or not isinstance(ids, list)
                    or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids))
                    or not set(ids) <= known or not isinstance(query, str) or len(query) > 256
                    or any(ord(c) < 32 for c in query)):
                return EvidenceSelection(passages)
            return EvidenceSelection([p for p in passages if p.source["id"] in ids], query.strip())
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
        # A failed ranking method cannot make an otherwise usable source ineligible.
        return EvidenceSelection(passages)


async def select_evidence(client, token, model, request, passages):
    """Compatibility entrypoint for frozen selection-only benchmark reports."""
    return (await plan_evidence(client, token, model, request, passages)).passages
