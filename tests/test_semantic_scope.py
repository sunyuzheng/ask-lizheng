"""Semantic candidates reach past passages that the question's source purpose excludes."""
import math
from array import array
from pathlib import Path

from server.retrieval import ContextIndex, is_yuzheng_basis
from server.semantic import DIMENSIONS, SemanticIndex

ROOT = Path(__file__).resolve().parents[1]


def test_an_eligible_passage_ranked_after_thirty_others_still_answers():
    index = ContextIndex(ROOT / "data/context", require_lock=True)
    index.load()
    outsiders = [i for i, d in enumerate(index.documents) if not is_yuzheng_basis(d) and d.text.strip()][:30]
    own = next(i for i, d in enumerate(index.documents) if is_yuzheng_basis(d))
    near, far = [1.0] + [0.0] * (DIMENSIONS - 1), [0.5, math.sqrt(0.75)] + [0.0] * (DIMENSIONS - 2)
    semantic = SemanticIndex(index.root, index.documents)
    semantic.vectors = array("f", near * len(outsiders) + far)
    semantic.document_indexes = [*outsiders, own]
    ranked = semantic._rank(array("f", near))
    assert [row["docindex"] for row in ranked][-1] == own
    passages = index.retrieve("毫不相干的提问词汇", intent="understand", semantic_candidates=ranked)
    assert own in {p.evidence["docindex"] for p in passages}
    assert all(is_yuzheng_basis(index.documents[p.evidence["docindex"]]) for p in passages)
