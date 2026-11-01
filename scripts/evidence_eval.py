#!/usr/bin/env python3
"""Offline evidence-span evaluation; only --refresh-vectors permits network access."""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib
import json
import math
import os
import re
import statistics
import sys
import time
import types
import unicodedata
from array import array
from collections import Counter, defaultdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.query_context import uses_previous_question

DEFAULT_FIXTURE = ROOT / "tests/fixtures/evidence-benchmark.json"
DEFAULT_CACHE = ROOT / ".local/rules-revision-20261101/query-vectors.json"
PUBLIC_VECTOR_SEED = ROOT / "tests/fixtures/evidence-query-vectors.json"


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def private_write(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def implementation(root: Path):
    """Load relative server imports under an isolated package, including dataclasses."""
    root = Path(root).resolve()
    if not (root / "server/retrieval.py").is_file():
        raise ValueError("Implementation root has no server/retrieval.py")
    name = "_evidence_impl_" + digest(str(root).encode())[:16]
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(root / "server")]
        sys.modules[name] = package
    return types.SimpleNamespace(root=root, name=name,
        retrieval=importlib.import_module(name + ".retrieval"),
        semantic=importlib.import_module(name + ".semantic"),
        answers=importlib.import_module(name + ".answers"))


def implementation_hash(root: Path) -> str:
    return digest(b"".join(path.name.encode() + b"\0" + path.read_bytes()
                            for path in sorted((root / "server").glob("*.py"))))


def normalize(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"(?m)^\s*[#>]+\s*", "", text)
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).replace("`", "").replace("*", "")).strip()


def source_body(path: Path, speaker: str | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        value = json.loads(text)
        if value.get("data_layer") != "raw" or not isinstance(value.get("units"), list):
            raise ValueError("Gold JSON must be a public raw dialogue")
        return normalize("\n".join(unit["text"] for unit in value["units"]
                                   if not speaker or unit.get("speaker_id") == speaker))
    if path.suffix != ".md":
        raise ValueError("Gold references must be Markdown or raw dialogue JSON")
    return normalize(re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S))


def load_fixture(path: Path, context_root: Path | None = None) -> dict:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    if fixture.get("schema_version") != 1 or not isinstance(fixture.get("refs"), dict) or not fixture.get("cases"):
        raise ValueError("Invalid evidence benchmark schema")
    ids, groups = set(), {}
    for case in fixture["cases"]:
        if (case["id"] in ids or case["split"] not in {"dev", "holdout"}
                or case["intent"] not in {"find", "understand", "apply"}
                or case["expect"] not in {"answered", "unsupported", "clarify", "any"}):
            raise ValueError("Invalid or duplicate benchmark case")
        ids.add(case["id"])
        if case["group"] in groups and groups[case["group"]] != case["split"]:
            raise ValueError("A paraphrase group leaks across dev and holdout")
        groups[case["group"]] = case["split"]
        if any(ref not in fixture["refs"] for ref in case["reference_ids"]):
            raise ValueError("Case references missing gold")
    if context_root is not None:
        manifest = json.loads((context_root / "release-manifest.json").read_text())
        files = {row["path"]: row for row in manifest["files"]}
        for ref_id, ref in fixture["refs"].items():
            relative = ref["path"]
            target = (context_root / relative).resolve()
            if (not relative.startswith("corpus/") or not target.is_relative_to(context_root.resolve())
                    or relative not in files or digest(target.read_bytes()) != files[relative]["sha256"]
                    or (ref.get("sha256") and ref["sha256"] != files[relative]["sha256"])
                    or not ref.get("quotes") or any(not isinstance(q, str) or not q.strip() for q in ref["quotes"])):
                raise ValueError("Gold source is not a pinned public primary body")
            body = source_body(target, ref.get("speaker_id") if target.suffix == ".json" else None)
            if any(normalize(quote) not in body for quote in ref["quotes"]):
                raise ValueError("Gold anchor does not occur in its declared public source or speaker")
            if ref.get("context"):
                if target.suffix == ".md":
                    valid = normalize(ref["context"]) in body
                else:
                    units = json.loads(target.read_text())["units"]
                    valid = True
                    for line in ref["context"].splitlines():
                        match = re.fullmatch(r"\[([^\]]+)\]\s+(.+)", line)
                        if not match or not any(unit.get("speaker_id") == match[1]
                                                and normalize(match[2]) in normalize(unit["text"]) for unit in units):
                            valid = False
                            break
                if not valid:
                    raise ValueError("Gold context does not match its pinned source: " + ref_id)
    return fixture


def select_cases(fixture: dict, split: str | None = None, ids: list[str] | None = None) -> list[dict]:
    if ids and set(ids) - {case["id"] for case in fixture["cases"]}:
        raise ValueError("Unknown benchmark case ID")
    return [case for case in fixture["cases"] if (not split or case["split"] == split)
            and (not ids or case["id"] in ids)]


def semantic_query(case: dict, semantic) -> str:
    question, history = case["question"], case.get("history", [])
    text = question + "\n" + case.get("context", "")
    if uses_previous_question(question, history):
        text += "\n" + history[-1]["question"]
    return next(semantic._byte_windows(text[:2500].strip(), semantic.MAX_INPUT_BYTES), "")


def encode(vector: array) -> str:
    vector = array("f", vector)
    if sys.byteorder != "little":
        vector.byteswap()
    return base64.b64encode(vector.tobytes()).decode("ascii")


def decode(value: str, dimensions: int) -> array:
    vector = array("f")
    vector.frombytes(base64.b64decode(value, validate=True))
    if sys.byteorder != "little":
        vector.byteswap()
    if len(vector) != dimensions or any(not math.isfinite(value) for value in vector) or abs(sum(value * value for value in vector) - 1) > .002:
        raise ValueError("Query cache has incompatible dimensions")
    return vector


async def vectors(impl, cases: list[dict], path: Path, refresh: bool = False, client=None, token: str = "") -> tuple[dict, dict]:
    semantic = impl.semantic
    read_path = path
    if not path.is_file() and path.resolve() == DEFAULT_CACHE.resolve() and PUBLIC_VECTOR_SEED.is_file():
        read_path = PUBLIC_VECTOR_SEED
    cache = json.loads(read_path.read_text()) if read_path.is_file() else {
        "schema_version": 1, "model": semantic.EMBEDDING_MODEL, "dimensions": semantic.DIMENSIONS, "queries": {}}
    if (cache.get("schema_version") != 1 or cache.get("model") != semantic.EMBEDDING_MODEL
            or cache.get("dimensions") != semantic.DIMENSIONS):
        raise ValueError("Query cache uses another embedding model or dimensions")
    queries = {digest(semantic_query(case, semantic).encode()): semantic_query(case, semantic) for case in cases}
    missing = [(key, query) for key, query in queries.items() if key not in cache["queries"]]
    if missing and not refresh:
        raise ValueError("Missing cached query vectors; explicitly run --refresh-vectors with AI_BUILDER_TOKEN")
    if missing:
        if not token or client is None:
            raise ValueError("Refreshing vectors requires an explicit environment token")
        for start in range(0, len(missing), semantic.MAX_BATCH):
            batch = missing[start:start + semantic.MAX_BATCH]
            values, _ = await semantic.request_embeddings(client, token, [query for _, query in batch], timeout=60)
            for (key, query), value in zip(batch, values):
                cache["queries"][key] = {"query": query, "vector": encode(value)}
        private_write(path, cache)
    result = {}
    for case in cases:
        query = semantic_query(case, semantic)
        row = cache["queries"][digest(query.encode())]
        if row.get("query") != query:
            raise ValueError("Cached query hash/content mismatch")
        result[case["id"]] = decode(row["vector"], semantic.DIMENSIONS)
    return result, {"model": cache["model"], "dimensions": cache["dimensions"],
                    "query_vector_sha256": digest(json.dumps({key: cache["queries"][key] for key in sorted(queries)}, sort_keys=True).encode())}


def prepare_case(impl, index, semantic, case: dict, vector: array) -> dict:
    started = time.perf_counter()
    question, context, history = case["question"], case.get("context", ""), case.get("history", [])
    clarification = impl.answers.attribution_clarification(question, context, history)
    candidates = semantic._rank(vector) if not clarification else []
    passages = index.retrieve(question, context, history, 12, candidates, intent=case["intent"]) if not clarification else []
    cards, result, route = [], clarification, "clarify" if clarification else "generate"
    if not clarification:
        named = index.participant_entities(index.retrieval_query(question, history)) if case["intent"] == "find" else set()
        if named:
            result = impl.answers.guest_reading_result(passages, sorted(index.entity_label(person) for person in named))
            route = "find-person"
        elif not any(not passage.discovery for passage in passages):
            result = (impl.answers.sources_only(passages, "找到这些节目，可以打开节目或原稿继续阅读。", "find")
                      if passages else impl.answers.unsupported_result(passages))
            route = "sources-only"
        else:
            cards = index.reasoning_bundle(question, context, history, passages, candidates)
    return {"passages": passages, "cards": cards, "result": result, "route": route,
            "retrieval_ms": round((time.perf_counter() - started) * 1000, 3)}


def evidence_metrics(impl, index, case: dict, refs: dict, prepared: dict) -> dict:
    passages = prepared["passages"]
    reference_rows = {}
    for ref_id in case["reference_ids"]:
        ref = refs[ref_id]
        matching = [p for p in passages if p.evidence.get("source_path") == ref["path"]
                    and (not ref.get("speaker_id") or p.evidence.get("speaker_id") == ref["speaker_id"])]
        excerpts = [normalize(impl.answers.model_evidence(p).get("excerpt", "")) for p in matching]
        hits = [any(normalize(quote) in excerpt for excerpt in excerpts) for quote in ref["quotes"]]
        reference_rows[ref_id] = {"path_hit": any(p.evidence.get("source_path") == ref["path"] for p in passages),
                                  "anchor_hits": hits, "any_anchor_hit": any(hits), "all_anchors_hit": all(hits)}
    participants = index.participant_entities(index.retrieval_query(case["question"], case.get("history", [])))
    host = index.names_host(index.retrieval_query(case["question"], case.get("history", [])))
    eligible = [case["intent"] == "find" or p.discovery or impl.retrieval.is_yuzheng_basis(index.documents[p.evidence["docindex"]])
                or (not host and impl.retrieval.is_participant_statement(index.documents[p.evidence["docindex"]], participants))
                for p in passages]
    families = Counter(p.evidence.get("source_family", "") for p in passages)
    anchors = [hit for row in reference_rows.values() for hit in row["anchor_hits"]]
    return {"references": reference_rows, "reference_count": len(reference_rows),
            "any_reference_hit": any(row["any_anchor_hit"] for row in reference_rows.values()) if reference_rows else None,
            "all_references_hit": all(row["any_anchor_hit"] for row in reference_rows.values()) if reference_rows else None,
            "anchor_hits": sum(anchors), "anchor_total": len(anchors),
            "path_hits": sum(row["path_hit"] for row in reference_rows.values()),
            "all_eligible": all(eligible), "budget_ok": len(passages) <= 12,
            "family_budget_ok": max(families.values(), default=0) <= 3,
            "passages": len(passages), "families": len(families), "route": prepared["route"],
            "retrieval_ms": prepared["retrieval_ms"]}


def summary(rows: list[dict]) -> dict:
    expected = [row for row in rows if row["reference_count"]]
    group_rows = defaultdict(list)
    for row in rows:
        if row["reference_count"]:
            group_rows[row["group"]].append(float(row["any_reference_hit"]))
    total = sum(row["anchor_total"] for row in rows)
    return {"cases": len(rows), "groups": len({row["group"] for row in rows}), "gold_cases": len(expected),
            "case_reference_hit_rate": statistics.mean(float(row["any_reference_hit"]) for row in expected) if expected else None,
            "case_all_references_hit_rate": statistics.mean(float(row["all_references_hit"]) for row in expected) if expected else None,
            "group_reference_hit_rate": statistics.mean(statistics.mean(values) for values in group_rows.values()) if group_rows else None,
            "anchor_coverage": sum(row["anchor_hits"] for row in rows) / total if total else None,
            "path_coverage": sum(row["path_hits"] for row in rows) / sum(row["reference_count"] for row in rows) if expected else None,
            "all_eligible": all(row["all_eligible"] for row in rows), "all_budgets_ok": all(row["budget_ok"] for row in rows),
            "family_budget_failures": [row["id"] for row in rows if not row["family_budget_ok"]],
            "mean_families": statistics.mean(row["families"] for row in rows) if rows else 0,
            "median_retrieval_ms": statistics.median(row["retrieval_ms"] for row in rows) if rows else 0}


def paired_changes(baseline: dict, candidate: dict) -> dict:
    for key in ("fixture_sha256", "context_commit", "query_vector_sha256"):
        if baseline.get(key) != candidate.get(key):
            raise ValueError("Paired reports have incompatible " + key)
    before = {row["id"]: row for row in baseline["cases"]}
    pairs = [{"id": row["id"], "group": row["group"],
              "anchor_hit_delta": row["anchor_hits"] - before[row["id"]]["anchor_hits"],
              "path_hit_delta": row["path_hits"] - before[row["id"]]["path_hits"],
              "families_delta": row["families"] - before[row["id"]]["families"],
              "retrieval_ms_delta": row["retrieval_ms"] - before[row["id"]]["retrieval_ms"],
              "reference_hit_delta": int(bool(row["any_reference_hit"])) - int(bool(before[row["id"]]["any_reference_hit"]))}
             for row in candidate["cases"] if row["id"] in before]
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[pair["group"]].append(pair)
    return {"paired_cases": len(pairs), "improved_cases": [p["id"] for p in pairs if p["anchor_hit_delta"] > 0],
            "regressed_cases": [p["id"] for p in pairs if p["anchor_hit_delta"] < 0], "cases": pairs,
            "groups": {group: {key: statistics.mean(p[key] for p in values)
                                 for key in ("anchor_hit_delta", "path_hit_delta", "reference_hit_delta", "families_delta", "retrieval_ms_delta")}
                       for group, values in sorted(grouped.items())}}


def add_inputs(parser):
    parser.add_argument("--implementation", type=Path, default=ROOT)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--vectors-cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--split", choices=("dev", "holdout"))
    parser.add_argument("--cases", nargs="+")


async def evaluate(args) -> dict:
    impl = implementation(args.implementation)
    fixture = load_fixture(args.fixture, impl.root / "data/context")
    cases = select_cases(fixture, args.split, args.cases)
    index = impl.retrieval.ContextIndex(impl.root / "data/context", require_lock=True)
    index.load()
    if fixture.get("context_commit") != index.source_revision:
        raise ValueError("Fixture and pinned context commits differ")
    semantic = index.semantic_index()
    if not semantic.ready:
        raise ValueError("Pinned semantic index is not ready")
    if args.refresh_vectors:
        async with httpx.AsyncClient() as client:
            cached, vector_meta = await vectors(impl, cases, args.vectors_cache, True, client, os.getenv("AI_BUILDER_TOKEN", ""))
    else:
        cached, vector_meta = await vectors(impl, cases, args.vectors_cache)
    rows = []
    for case in cases:
        prepared = prepare_case(impl, index, semantic, case, cached[case["id"]])
        rows.append({"id": case["id"], "group": case["group"], "split": case["split"], "category": case["category"],
                     **evidence_metrics(impl, index, case, fixture["refs"], prepared)})
    report = {"schema_version": 1, "kind": "evidence-evaluation", "implementation": str(impl.root),
              "implementation_sha256": implementation_hash(impl.root),
              "fixture_sha256": digest(args.fixture.read_bytes()), "context_commit": index.source_revision,
              **vector_meta, "summary": summary(rows), "cases": rows,
              "method": "Exact normalized source-anchor presence in actual model excerpts; paths reported separately. Retrieval coverage is not answer accuracy."}
    if args.compare:
        report["paired_changes"] = paired_changes(json.loads(args.compare.read_text()), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_inputs(parser)
    parser.add_argument("--output", type=Path, default=ROOT / ".local/rules-revision-20261101/evidence.json")
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--refresh-vectors", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(evaluate(args))
        private_write(args.output, report)
        print(json.dumps(report["summary"], ensure_ascii=False))
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
