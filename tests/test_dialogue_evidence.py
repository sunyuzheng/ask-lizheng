"""Dialogue context is mechanically bound; agreement remains a semantic judgment."""
import copy
import pytest
from server.dialogue_evidence import render, supports_speaker, window
from server import answers
from server.retrieval import Passage


def unit(i, speaker, text, act="statement", status="reviewed-source-attribution"):
    return dict(id=f"cue-{i}", speaker_id=speaker, speaker_name=speaker,
                speaker_status=status, speech_act=act, text=text, start_seconds=i * 10, end_seconds=i * 10 + 5)


def test_only_visible_attributed_statements_establish_speaker_eligibility():
    e = {"dialogue_units": [unit(1,"guest:alpha","桥梁条件。"), unit(2,"yuzheng","真的吗？","question"),
                            unit(3,"unknown","我同意。",status="unresolved")]}
    assert supports_speaker(e,"guest:alpha")
    assert not supports_speaker(e,"yuzheng")
    assert not supports_speaker(e,"unknown")
    e["dialogue_units"][1]["speech_act"] = "statement"
    assert supports_speaker(e,"yuzheng")


def test_window_is_contiguous_balanced_and_does_not_join_distant_agreement():
    units = [unit(i,"yuzheng" if i % 2 else "guest:alpha", "背景" * 20) for i in range(80)]
    units[40]["text"] = "需要核对的目标论点"
    units[2]["text"] = "同意另外一个问题"
    selected = window(units,["目标论点"],{"目标论点":10},budget=300)
    indices = [u["id"] for u in selected]
    assert "cue-40" in indices and "cue-2" not in indices
    assert selected == units[units.index(selected[0]):units.index(selected[-1])+1]
    assert len(render(selected)) > 0
    assert sum(len(u["text"]) for u in selected) <= 300


@pytest.mark.parametrize("act", ["question","acknowledgement","joke","reported-other","introduction"])
def test_host_attribution_guard_does_not_upgrade_a_question_or_polite_response(act):
    a = answers.ModelAnswer.model_validate({"status":"answered","summary":"立正认为这个条件成立。[S1]",
        "sections":[{"heading":"条件","body":"嘉宾的判断。[S1]","source_ids":["S1"],"kind":"source"}],
        "followups":[],"clarifying_questions":[],"limitations":"","source_reasons":[],"slug":"conditions"})
    p = Passage({"id":"S1","title":"合成对话","speaker_id":"unknown"},
                {"id":"S1","dialogue_units":[unit(1,"guest:alpha","这个条件成立。"),unit(2,"yuzheng","回应",act)]}, False)
    assert answers.misattributed(a,[p])
    p.evidence["dialogue_units"][1]["speech_act"] = "statement"
    assert not answers.misattributed(a,[p])
