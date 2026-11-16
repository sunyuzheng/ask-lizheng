"""Request-local evidence reading: global backstop, source routes, one follow-up.

No additional index or taxonomy. Public source eligibility remains ContextIndex's
responsibility; a navigation hit or gap cannot become a person's assertion.
"""
from __future__ import annotations

import time
import asyncio
from dataclasses import replace

from .retrieval import FLAG_BODY, FLAG_DIALOGUE, FLAG_STATEMENT
from .retrieval_trace import ID, RetrievalTrace

MAX_PASSAGES = 12  # Every archive/public projection shares the same source ceiling.


def merge_passages(primary, extra, *, limit=MAX_PASSAGES):
    """Keep existing windows intact; admit at most one new source per family.

    ContextIndex owns within-family continuous-dialogue/overlap checks. This
    cross-read merger conservatively keeps its first read for an existing family.
    """
    result, families = list(primary), {p.evidence["source_family"] for p in primary}
    for passage in extra:
        family = passage.evidence["source_family"]
        if family not in families and len(result) < limit:
            result.append(passage)
            families.add(family)
    return [replace(p, source={**p.source, "id": f"S{n}"}, evidence={**p.evidence, "id": f"S{n}"})
            for n, p in enumerate(result[:limit], 1)]


class ContextReader:
    def __init__(self, index, question, context="", history=None, intent="find", *, allow_supplement=False):
        self.index, self.question, self.context = index, question, context
        self.history, self.intent = history, intent
        self.trace = RetrievalTrace(index)
        self.gaps = []
        self.supplemented = False
        self.allow_supplement = allow_supplement

    def _gaps(self, diagnostics):
        for i in diagnostics.get("blocked", []):
            row = self.index.briefs[i]
            doc = self.index.documents[i]
            source_id = doc.source_id if ID.fullmatch(doc.source_id) else doc.id
            reason = ("missing_text" if not row.flags & FLAG_BODY else
                      "speaker_unresolved" if row.speaker_id in {"", "unknown"} and not row.flags & (FLAG_DIALOGUE | FLAG_STATEMENT)
                      else "not_answer_material")
            self.trace.gap(source_id, reason)
            gap = {"source_id": source_id, "reason": reason}
            if gap not in self.gaps and len(self.gaps) < 12:
                self.gaps.append(gap)

    def retrieve(self, semantic=None, *, initial=False):
        start, diagnostic = time.monotonic(), {}
        passages = self.index.retrieve(self.question, self.context, self.history, 12, semantic,
                                       intent=self.intent, diagnostics=diagnostic)
        self._gaps(diagnostic)
        self.trace.step("lexical" if initial else "hybrid", passages, (time.monotonic() - start) * 1000)
        return passages

    def supplement(self, query, selected, semantic=None):
        if self.supplemented:
            return selected
        self.supplemented = True
        start, diagnostic = time.monotonic(), {}
        extra = self.index.retrieve(query, "", None, 4, semantic, intent=self.intent, diagnostics=diagnostic)
        self._gaps(diagnostic)
        families = self.index.navigation_families(query)
        routed = self.index.retrieve(query, "", None, 2, semantic, intent=self.intent,
                                     source_families=families) if families else []
        # The exploratory every-question directory expansion had no anchor gain
        # on 76 frozen cases. Only a selector-identified missing facet earns this
        # read; global retrieval remains a backstop beside the source route.
        routed_new = [p for p in routed if p.evidence["source_family"] not in
                      {e.evidence["source_family"] for e in extra}]
        self.trace.step("navigation", routed_new, (time.monotonic() - start) * 1000)
        extra = merge_passages(extra, routed_new, limit=6)
        merged = merge_passages(selected, extra)
        old = {p.source.get("fragment_id") for p in selected}
        self.trace.step("supplement", [p for p in merged if p.source.get("fragment_id") not in old],
                        (time.monotonic() - start) * 1000)
        return merged

    def selected(self, passages):
        self.trace.step("selected", passages, reason="selected")

    async def select(self, client, token, model, request, passages, semantic, deadline, progress=None):
        """Shared production/evaluation path. One selector, at most one new search."""
        from .evidence_selection import plan_evidence
        selection = await plan_evidence(client, token, model, request, passages,
                                        allow_supplement=self.allow_supplement)
        passages = selection.passages
        self.selected(passages)
        if self.allow_supplement and selection.search_query and deadline - time.monotonic() >= 15:
            if progress:
                await progress({"stage": "matching", "message": "正在补查问题中尚缺依据的部分…"})
            try:
                async with asyncio.timeout(6):
                    candidates = await semantic.candidates(client, token, selection.search_query)
            except TimeoutError:
                candidates = []
            passages = await asyncio.to_thread(self.supplement, selection.search_query, passages, candidates)
        return passages

    def cited(self, result, passages):
        fragments = {s.get("fragment_id") for s in result.get("sources", [])}
        self.trace.step("cited", [p for p in passages if p.source.get("fragment_id") in fragments], reason="cited")
        return self.trace.snapshot()
