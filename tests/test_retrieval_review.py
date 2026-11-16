import json
from pathlib import Path

import pytest
from scripts import review_retrieval as review
from scripts.evidence_eval import compatible_context


def receipt():
    def step(stage, items):
        return {"stage": stage, "elapsed_ms": 1, "items": [
            {"source_id": s, "fragment_id": f, "rank": n} for n, (s,f) in enumerate(items, 1)]}
    return {"v":1,"context_commit":"a"*40,"context_release":"b"*64,"engine_sha256":"c"*64,
        "steps":[step("lexical",[("one","one:1"),("two","two:1")]),
                 step("hybrid",[("one","one:2")]),step("selected",[("one","one:2")]),
                 step("cited",[])],"gaps":[{"source_id":"three","reason":"speaker_unresolved"}]}


def test_local_review_distinguishes_source_drop_and_window_change_without_copying_text():
    r=review.review_record({"record_id":"synthetic", "question":"do not copy this", "answer":{"summary":"private"}, "retrieval_trace":receipt()})
    assert r["transitions"][0] == {"from":"lexical","to":"hybrid","missing_sources":["two"],"changed_or_removed_windows":["one:1"]}
    assert r["transitions"][-1]["missing_sources"] == ["one"]
    assert "question" not in r and "answer" not in r
    assert r["gaps"][0]["reason"] == "speaker_unresolved"
    assert review.review_record({"record_id":"old"})["trace_status"] == "not_recorded_or_invalid"


def test_review_is_private_bounded_and_refuses_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(review,"ROOT",tmp_path)
    folder=tmp_path/".local";folder.mkdir()
    src=folder/"export.json";out=folder/"review.json"
    src.write_text(json.dumps({"records":[{"record_id":"synthetic","question":"private","retrieval_trace":receipt()}]}))
    argv=["--input",str(src),"--output",str(out)]
    assert review.main(argv)==0
    assert "private" not in out.read_text() and out.stat().st_mode & 0o777 == 0o600
    old=out.read_bytes();assert review.main(argv)==1 and out.read_bytes()==old
    assert review.main(["--input",str(src),"--output",str(tmp_path/"public.json")])==1


def test_compatible_revision_reuse_requires_explicit_opt_in_and_all_gold_hashes():
    from types import SimpleNamespace
    fixture={"context_commit":"a"*40,"refs":{"r":{"sha256":"c"*64}}}
    current=SimpleNamespace(source_revision="b"*40)
    with pytest.raises(ValueError):compatible_context(fixture,current)
    compatible_context(fixture,current,True)
    del fixture["refs"]["r"]["sha256"]
    with pytest.raises(ValueError):compatible_context(fixture,current,True)


@pytest.mark.parametrize("count", [0,1,2])
def test_review_accepts_the_dashboard_actual_jsonl_export(tmp_path, monkeypatch, count):
    monkeypatch.setattr(review,"ROOT",tmp_path)
    folder=tmp_path/".local";folder.mkdir()
    src=folder/"archive.jsonl";out=folder/"review.json"
    src.write_text("".join(json.dumps({"record_id":f"synthetic-{n}","question":"private", "retrieval_trace":receipt()})+"\n" for n in range(count)))
    assert review.main(["--input",str(src),"--output",str(out)])==0
    value=json.loads(out.read_text())
    assert len(value["records"])==count and value["network_requests"]==0
    assert "question" not in out.read_text()
