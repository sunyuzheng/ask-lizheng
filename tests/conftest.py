"""Keep existing API lifecycle tests focused on their generation/storage dependency."""
import pytest


@pytest.fixture(autouse=True)
def isolate_evidence_selection(request, monkeypatch):
    if request.node.get_closest_marker("evidence_selection"):
        return
    async def keep_candidates(client, token, model, request, passages, **kwargs):
        from server.evidence_selection import EvidenceSelection
        return EvidenceSelection(passages)
    monkeypatch.setattr("server.evidence_selection.plan_evidence", keep_candidates)
