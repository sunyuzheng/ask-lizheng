#!/usr/bin/env python3
"""Retrieval evaluation for ordinary answers, with no model calls.

tests/fixtures/retrieval-eval.json holds everyday questions and each question's query vector,
computed once with the index's embedding model. For each question the evaluation runs the same
lexical, semantic and card retrieval as /api/ask and checks two things: the passages it would answer
from are all eligible for an ordinary answer, and a question written for a reasoning card reaches at
least one of that card's basis sources. tests/test_retrieval_eval.py gates every update on it.

  python scripts/retrieval_eval.py                    print the report
  python scripts/retrieval_eval.py --refresh-vectors  embed new or changed questions (AI_BUILDER_TOKEN)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
from array import array
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

import httpx

from server.retrieval import ContextIndex, is_participant_statement, is_yuzheng_basis
from server.semantic import DIMENSIONS, EMBEDDING_MODEL, MAX_BATCH, SemanticIndex, request_embeddings

FIXTURE = PROJECT / "tests/fixtures/retrieval-eval.json"
# Share of card questions that must reach their card's basis; a lower rate fails the update.
CARD_HIT_RATE = 0.85
# Eligible semantic candidates each question must keep after its source purpose is applied.
MIN_ELIGIBLE_SEMANTIC = 10


def question_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decode(value: str) -> array:
    vector = array("f")
    vector.frombytes(base64.b64decode(value))
    if sys.byteorder != "little":
        vector.byteswap()
    if len(vector) != DIMENSIONS:
        raise ValueError("Stored query vector has the wrong dimension")
    return vector


def encode(vector: array) -> str:
    copy = array("f", vector)
    if sys.byteorder != "little":
        copy.byteswap()
    return base64.b64encode(copy.tobytes()).decode("ascii")


def load_fixture() -> dict:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if data.get("model") != EMBEDDING_MODEL or data.get("dimensions") != DIMENSIONS:
        raise ValueError("Evaluation vectors were made with another embedding model")
    return data


def evaluate(index: ContextIndex | None = None) -> dict:
    data = load_fixture()
    if index is None:
        index = ContextIndex(PROJECT / "data/context", require_lock=True)
        index.load()
    semantic = index.semantic_index()
    if not semantic.ready:
        raise ValueError("The pinned semantic index is not ready: " + semantic.disabled_reason)
    basis_paths = {card_id: {index.documents[ref.docindex].path for ref in refs if ref.role == "basis"}
                   for card_id, refs in index.architecture.references.items()}
    rows = []
    for item in data["items"]:
        if item.get("question_sha256") != question_hash(item["question"]) or not item.get("vector"):
            raise ValueError(f"{item['id']}: query vector is missing or stale; run --refresh-vectors")
        candidates = semantic._rank(decode(item["vector"]))
        passages = index.retrieve(item["question"], limit=12, semantic_candidates=candidates, intent=item["intent"])
        eligible = [row for row in candidates if is_yuzheng_basis(index.documents[row["docindex"]])]
        paths = {passage.evidence["source_path"] for passage in passages}
        rows.append({
            "id": item["id"],
            "passages": len(passages),
            "all_eligible": all(is_yuzheng_basis(index.documents[p.evidence["docindex"]])
                                or (not index.names_host(item["question"])
                                    and is_participant_statement(index.documents[p.evidence["docindex"]], index.participant_entities(item["question"])))
                                for p in passages),
            "eligible_semantic": len(eligible),
            "card": item.get("card"),
            "card_hit": bool(paths & basis_paths.get(item["card"], set())) if item.get("card") else None,
        })
    cards = [row for row in rows if row["card"]]
    return {"rows": rows, "card_hit_rate": sum(row["card_hit"] for row in cards) / max(len(cards), 1),
            "all_eligible": all(row["all_eligible"] for row in rows),
            "min_eligible_semantic": min(row["eligible_semantic"] for row in rows)}


async def refresh(token: str) -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    stale = [item for item in data["items"] if item.get("question_sha256") != question_hash(item["question"]) or not item.get("vector")]
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for start in range(0, len(stale), MAX_BATCH):
            batch = stale[start:start + MAX_BATCH]
            vectors, _ = await request_embeddings(client, token, [item["question"] for item in batch], timeout=45)
            for item, vector in zip(batch, vectors):
                item["vector"], item["question_sha256"] = encode(vector), question_hash(item["question"])
    FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(stale)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh-vectors", action="store_true", help="Embed new or changed questions (needs AI_BUILDER_TOKEN)")
    args = parser.parse_args(argv)
    if args.refresh_vectors:
        token = os.environ.get("AI_BUILDER_TOKEN", "")
        if not token:
            print("Set AI_BUILDER_TOKEN to embed the evaluation questions.", file=sys.stderr)
            return 1
        print(json.dumps({"embedded": asyncio.run(refresh(token))}))
        return 0
    report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = report["all_eligible"] and report["card_hit_rate"] >= CARD_HIT_RATE and report["min_eligible_semantic"] >= MIN_ELIGIBLE_SEMANTIC
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
