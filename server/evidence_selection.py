"""Semantic selection is a bounded retrieval default, never a source-ownership boundary."""
from __future__ import annotations

import asyncio
import json
import httpx

SELECTION_SECONDS = 8
URL = "https://space.ai-builders.com/backend/v1/chat/completions"


def empty_answer():
    from .answers import ModelAnswer
    return ModelAnswer(status="unsupported", summary="当前候选片段不足以直接支持这个问题；这不表示作者从未谈过。",
                       sections=[], followups=[], clarifying_questions=[], slug="source-gap")


async def select_evidence(client, token, model, request, passages):
    from .answer_contract import CRITERIA, model_evidence
    from .answers import model_options
    payload = {
        "model": model, "temperature": 0, "max_tokens": 1200,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "为有来源的问答挑选证据。保留能直接支撑回答、关键条件或必要分歧的excerpt，不为了凑数量保留旁枝。遵守同一回答合同：\n"
                + "\n".join(CRITERIA[k]["instruction"] for k in ("relevance", "faithfulness", "attribution"))
                + "\n只能选给出的编号，不补写事实；没有相关正文可以为空。只输出JSON：{\"source_ids\":[\"S1\"]}。"},
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
            if (set(value) != {"source_ids"} or not isinstance(ids, list)
                    or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids))
                    or not set(ids) <= known):
                return passages
            return [p for p in passages if p.source["id"] in ids]
    except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError):
        # A failed ranking method cannot make an otherwise usable source ineligible.
        return passages
