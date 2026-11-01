"""Policy ownership and source bindings, including fields outside section bodies."""
import json

import pytest

from server import answer_contract as contract
from server import answers
from server.retrieval import Passage


def sample_passage(*, own=False, act="statement"):
    source = {"id": "S1", "title": "Synthetic source", "author": "Synthetic Alpha",
              "speaker_id": "yuzheng" if own else "guest:alpha", "speaker_name": "立正" if own else "Synthetic Alpha",
              "speaker_status": "reviewed-source-attribution", "excerpt": "同样条件才可比较。", "url": "https://example.org/source"}
    evidence = {**source, "data_layer": "raw", "content_origin": "yuzheng-spoken-source" if own else "guest-spoken-source",
                "speech_act": act, "host_names": ["立正", "Yuzheng Sun"], "source_snapshot_sha256": "a" * 64,
                "source_family": "synthetic", "yuzheng_stance_weight": "direct" if own else "not-evidence"}
    return Passage(source, evidence, False)


def sample_answer():
    return answers.ModelAnswer.model_validate(contract.EXAMPLE)


def test_complete_example_validates_and_all_judged_outcomes_have_one_rubric():
    answer = sample_answer()
    answers.validate_answer(answer, [sample_passage()])
    assert set(contract.JUDGE_CRITERIA) == {k for k, v in contract.CRITERIA.items() if v["judge"]}
    assert all(v["judge"] or v["server"] for v in contract.CRITERIA.values())
    assert set(answers.strict_schema()["required"]) == set(answer.model_dump())


def test_public_engine_identity_matches_the_offline_evaluation_code_hash():
    from pathlib import Path
    from scripts.evidence_eval import implementation_hash
    assert contract.engine_fingerprint() == implementation_hash(Path(__file__).resolve().parents[1])


def test_audit_register_covers_all97_rules_without_creating_extra_criteria():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    register = json.loads((root / "docs/answer-rule-register.json").read_text())
    expected = {f"{prefix}{n}" for prefix, count in {"P":40,"R":28,"G":4,"V":13,"K":4,"E":5,"D":3}.items()
                for n in range(1, count + 1)}
    assert {row["id"] for row in register["rules"]} == expected
    assert len(register["rules"]) == len(expected)
    assert all(row["criterion"] in contract.CRITERIA for row in register["rules"])


def test_legacy_judge_uses_same_outcomes_and_catalog_names():
    from scripts import answer_eval
    assert set(answer_eval.CRITERIA) == set(contract.JUDGE_CRITERIA)
    for name in answer_eval._host_names:
        assert name in answer_eval.JUDGE_PROMPT
    assert "不能衡量资料漏检" in answer_eval.JUDGE_PROMPT


def test_all_models_share_semantic_policy_and_actual_evidence():
    p = sample_passage()
    p.source["source_type"] = "community-post"
    for model in ("gpt-5", "deepseek-v4-flash", "deepseek-v4-pro", "grok-4.5"):
        payload = answers.answer_payload(model, {"question": "合成问题", "reasoning_cards": [{"thesis": "导航中的无依据结论"}]}, [p])
        assert payload["messages"][0]["content"].startswith(contract.system_prompt())
        suffix = payload["messages"][0]["content"][len(contract.system_prompt()):]
        assert not suffix or suffix.startswith("\n为逐段展示") or suffix.startswith("\n输出 JSON")
        assert "Flash" not in suffix
        assert json.loads(payload["messages"][1]["content"])["sources"] == [contract.model_evidence(p)]
        assert "reasoning_cards" not in json.loads(payload["messages"][1]["content"])
        assert "导航中的无依据结论" not in payload["messages"][1]["content"]


def test_compact_annotations_do_not_replace_full_provenance():
    p = sample_passage()
    p.source["source_type"] = "community-post"
    p.source["attribution_note"] = "英文原作与中文AI改写归属不同，这是来源本身的说明。"
    p.evidence["section"] = "Synthetic Alpha在本期的陈述"
    value = contract.model_evidence(p)
    assert "归属已核对" in " ".join(value["attribution"]["notes"])
    assert "句间可能隔着其他发言" in " ".join(value["attribution"]["notes"])
    assert p.source["attribution_note"] in value["attribution"]["notes"]
    assert "Synthetic Alpha" in value["attribution"]["basis"]
    assert value["source_type"] == "community-post"
    assert "source_snapshot_sha256" not in value
    assert p.evidence["source_snapshot_sha256"] == "a" * 64
    assert value["excerpt"] == p.evidence["excerpt"]


@pytest.mark.parametrize("field", ["summary", "heading", "body", "limitations", "followups", "clarifying_questions", "reason"])
@pytest.mark.parametrize("claim", ["立正认为这个条件重要。", "Yuzheng Sun建议先比较条件。"])
def test_host_claims_in_every_visible_field_require_own_evidence(field, claim):
    a = sample_answer()
    if field in {"heading", "body"}: setattr(a.sections[0], field, claim)
    elif field in {"followups", "clarifying_questions"}: setattr(a, field, [claim])
    elif field == "reason": a.source_reasons[0].reason = claim
    else: setattr(a, field, claim)
    assert answers.misattributed(a, [sample_passage()])
    assert not answers.misattributed(a, [sample_passage(own=True)])


@pytest.mark.parametrize("act", ["question", "joke", "reported-other", "introduction"])
def test_attributed_nonstatement_cannot_prove_host_view(act):
    a = sample_answer()
    a.summary = "立正认为这些条件成立。"
    assert answers.misattributed(a, [sample_passage(own=True, act=act)])
    a.summary = "这些片段尚不能确认立正的观点。"
    assert not answers.misattributed(a, [sample_passage(own=True, act=act)])


def test_scoped_absence_is_allowed_without_any_host_source():
    a = sample_answer()
    a.limitations = "这些片段无法证明立正的观点。"
    assert not answers.misattributed(a, [sample_passage()])


def test_byline_and_publisher_are_not_mistaken_for_a_host_view():
    a = sample_answer()
    a.sections[0].body = "文章署名 Yuzheng Sun，发布在合成博客。[S1]"
    assert not answers.misattributed(a, [sample_passage()])
    a.sections[0].body = "Yuzheng Sun提到应该采用这个方案。[S1]"
    assert answers.misattributed(a, [sample_passage()])


def test_explicit_ids_can_be_bound_without_inventing_or_accepting_discovery_evidence():
    a = sample_answer()
    a.sections[0].source_ids = []
    answers.normalize_source_bindings(a, [sample_passage()])
    answers.validate_answer(a, [sample_passage()])
    assert a.sections[0].source_ids == ["S1"]
    unknown = sample_answer()
    unknown.sections[0].body += "未知编号[S9]。"
    answers.normalize_source_bindings(unknown, [sample_passage()])
    with pytest.raises(answers.InvalidAnswer): answers.validate_answer(unknown, [sample_passage()])
    discovery = sample_passage()
    discovery.discovery = True
    with pytest.raises(answers.InvalidAnswer): answers.validate_answer(a, [discovery])
