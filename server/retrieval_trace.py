"""Private, bounded provenance receipts. Never persist queries, text or reasoning."""
from __future__ import annotations

import json
import re

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:#-]{0,239}\Z")
STAGES = {"lexical", "hybrid", "navigation", "supplement", "selected", "cited"}
REASONS = {"candidate", "selected", "cited", "speaker_unresolved", "not_answer_material", "not_selected"}
GAPS = {"missing_text", "speaker_unresolved", "not_answer_material", "coverage_gap"}
LIMIT = 16384


def normalize_trace(value):
    """Fail closed and return a frozen receipt, or None. Optional diagnostics must
    never prevent a durable answer from being saved by an older/strict gateway.
    """
    if not isinstance(value, dict) or set(value) != {"v", "context_commit", "context_release", "engine_sha256", "steps", "gaps"}:
        return None
    if type(value["v"]) is not int or value["v"] != 1:
        return None
    for field, length in (("context_commit", 40), ("context_release", 64), ("engine_sha256", 64)):
        if not isinstance(value[field], str) or not re.fullmatch(f"[a-f0-9]{{{length}}}", value[field]):
            return None
    if not isinstance(value["steps"], list) or len(value["steps"]) > 8 or not isinstance(value["gaps"], list) or len(value["gaps"]) > 12:
        return None
    for step in value["steps"]:
        if (not isinstance(step, dict) or set(step) != {"stage", "items", "elapsed_ms"}
                or not isinstance(step["stage"], str) or step["stage"] not in STAGES or type(step["elapsed_ms"]) is not int
                or not 0 <= step["elapsed_ms"] <= 600000
                or not isinstance(step["items"], list) or len(step["items"]) > 24):
            return None
        for item in step["items"]:
            if (not isinstance(item, dict) or not {"source_id", "fragment_id", "rank"} <= set(item)
                    or set(item) - {"source_id", "fragment_id", "rank", "reason"}
                    or any(not isinstance(item[k], str) or not ID.fullmatch(item[k]) for k in ("source_id", "fragment_id"))
                    or type(item["rank"]) is not int or not 1 <= item["rank"] <= 20000
                    or ("reason" in item and (not isinstance(item["reason"], str) or item["reason"] not in REASONS))):
                return None
    for gap in value["gaps"]:
        if (not isinstance(gap, dict) or set(gap) != {"source_id", "reason"}
                or not isinstance(gap["source_id"], str) or not ID.fullmatch(gap["source_id"])
                or not isinstance(gap["reason"], str) or gap["reason"] not in GAPS):
            return None
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.loads(raw) if len(raw.encode()) <= LIMIT else None


class RetrievalTrace:
    def __init__(self, index):
        from .answer_contract import engine_fingerprint
        meta = index.metadata()
        self.value = {"v": 1, **{k: meta[k] for k in ("context_commit", "context_release")},
                      "engine_sha256": engine_fingerprint(), "steps": [], "gaps": []}

    def step(self, stage, passages, elapsed_ms=0, reason="candidate"):
        items = [{"source_id": p.evidence.get("source_id") if ID.fullmatch(p.evidence.get("source_id", "")) else p.evidence.get("document_id", ""),
                  "fragment_id": p.source.get("fragment_id") if ID.fullmatch(p.source.get("fragment_id", "")) else p.evidence.get("document_id", ""),
                  "rank": n, "reason": reason} for n, p in enumerate(passages[:24], 1)]
        self.value["steps"].append({"stage": stage, "items": items,
                                    "elapsed_ms": min(600000, max(0, int(elapsed_ms)))})
        self.value["steps"] = self.value["steps"][:8]

    def gap(self, source_id, reason):
        row = {"source_id": source_id, "reason": reason}
        if row not in self.value["gaps"] and len(self.value["gaps"]) < 12:
            self.value["gaps"].append(row)

    def snapshot(self):
        # Trim lower ranked candidates first, preserving every stage and final
        # citation receipts within the agreed byte envelope.
        value = json.loads(json.dumps(self.value))
        while len(json.dumps(value, separators=(",", ":")).encode()) > LIMIT:
            candidates = [s for s in value["steps"] if s["stage"] != "cited" and len(s["items"]) > 1]
            if not candidates:
                return None
            max(candidates, key=lambda s: len(s["items"]))["items"].pop()
        return normalize_trace(value)
