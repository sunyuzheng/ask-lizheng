"""Every update must keep ordinary answers on eligible sources and reach the cards' basis."""
import importlib.util
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("retrieval_eval", PROJECT / "scripts/retrieval_eval.py")
retrieval_eval = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retrieval_eval)


def test_retrieval_evaluation_holds_on_the_pinned_release():
    report = retrieval_eval.evaluate()
    assert report["all_eligible"]
    assert report["card_hit_rate"] >= retrieval_eval.CARD_HIT_RATE, [r for r in report["rows"] if r["card_hit"] is False]
    assert report["min_eligible_semantic"] >= retrieval_eval.MIN_ELIGIBLE_SEMANTIC
