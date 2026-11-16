#!/usr/bin/env python3
"""Review owner-exported retrieval receipts locally. No models or network calls.

This compares recorded stages; it cannot reconstruct a missing historical trace,
the visitor's unretained background, or a different version's semantic ranking.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.retrieval_trace import normalize_trace
from scripts.evidence_eval import private_write


def review_record(record):
    trace = normalize_trace(record.get("retrieval_trace"))
    result = {"record_id": record.get("record_id"), "trace_status": "recorded" if trace else "not_recorded_or_invalid"}
    if trace is None:
        return result
    stages = {}
    for step in trace["steps"]:
        stages[step["stage"]] = step["items"]
    transitions = []
    order = [s for s in ("lexical", "hybrid", "selected", "supplement", "cited") if s in stages]
    for before, after in zip(order, order[1:]):
        source_ids = {i["source_id"] for i in stages[after]}
        fragments = {(i["source_id"], i["fragment_id"]) for i in stages[after]}
        transitions.append({"from": before, "to": after,
            "missing_sources": sorted({i["source_id"] for i in stages[before]} - source_ids),
            "changed_or_removed_windows": [i["fragment_id"] for i in stages[before]
                if i["source_id"] in source_ids and (i["source_id"], i["fragment_id"]) not in fragments]})
    return {**result, **{k: trace[k] for k in ("context_commit", "context_release", "engine_sha256")},
            "transitions": transitions, "gaps": trace["gaps"]}


def private_path(path):
    if not path.resolve().is_relative_to((ROOT / ".local").resolve()):
        raise ValueError("Owner exports and reports must stay under this repository's .local directory")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="An owner export page: {records:[...]}")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    try:
        private_path(args.input); private_path(args.output)
        if not 1 <= args.limit <= 500 or args.input.stat().st_size > 16 * 1024 * 1024 or args.output.exists():
            raise ValueError("Use up to 500 records / 16 MB and a new output file")
        data = json.loads(args.input.read_text())
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            raise ValueError("Expected an owner export page with records")
        rows = [review_record(r) for r in records[:args.limit]]
        private_write(args.output, {"v": 1, "mode": "recorded-stages-only", "network_requests": 0,
            "records": rows, "remaining_in_page": max(0, len(records)-args.limit)})
        print(json.dumps({"reviewed": len(rows), "with_trace": sum(r["trace_status"] == "recorded" for r in rows),
                          "network_requests": 0}))
        return 0
    except (ValueError, OSError):
        # Do not print file paths, questions or parser excerpts from a private export.
        print("Review stopped: check private input/output paths, bounds and export format.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
