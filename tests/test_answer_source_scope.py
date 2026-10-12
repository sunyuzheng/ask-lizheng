"""Source purpose is enforced before lexical, semantic and navigation fusion."""
import json
from types import SimpleNamespace

from server.retrieval import ContextIndex, is_yuzheng_basis
from test_backend import context_pack, index, source_file
from test_dialogue_layers import dialogue


def test_answer_scopes_reject_guest_and_derived_semantic_hits(index):
    outsiders = [i for i, d in enumerate(index.documents) if not is_yuzheng_basis(d)]
    semantic = [{"docindex": i, "score": .99} for i in outsiders]
    for intent in ("understand", "apply"):
        passages = index.retrieve("职业选择能力作品", intent=intent, semantic_candidates=semantic)
        assert passages
        assert all(is_yuzheng_basis(index.documents[p.evidence["docindex"]]) for p in passages)
        assert not any(p.source["author"] == "Guest Author" for p in passages)
    found = index.retrieve("职业选择能力作品", intent="find", semantic_candidates=semantic)
    assert any(p.source["author"] == "Guest Author" for p in found)


def test_navigation_cannot_inject_an_outside_source(index):
    outside = next(i for i, d in enumerate(index.documents) if d.author == "Guest Author")
    index.architecture = SimpleNamespace(route=lambda *a: ["synthetic"],
        linked_candidates=lambda ids: [{"docindex": outside, "weight": 1, "focus_terms": ["职业"]}])
    assert all(p.evidence["docindex"] != outside for p in index.retrieve("职业选择", intent="understand"))


def test_own_statements_survive_but_own_questions_and_unknown_speech_do_not(context_pack):
    p = dialogue(context_pack); data = json.loads(p.read_text())
    data["units"][0]["speech_act"] = "question"
    data["units"].append({"id": "own-statement", "start_seconds": 10, "end_seconds": 12,
        "text": "归属探针，我的明确判断。", "speaker_id": "yuzheng", "speaker_name": "立正",
        "speaker_status": "reviewed-source-attribution", "speech_act": "statement"})
    p.write_text(json.dumps(data, ensure_ascii=False)); ix = ContextIndex(context_pack); ix.load()
    result = ix.retrieve("归属探针", intent="understand")
    assert len(result) == 1
    assert result[0].source["speaker_id"] == "yuzheng"
    assert result[0].source["speech_act"] == "statement"


def test_named_guest_without_own_speech_returns_programme_entry(context_pack):
    source_file(context_pack, "corpus/videos", "solo.md", {"id": "solo-own", "title": "合成自讲",
        "source_type": "video-transcript", "source_url": "https://example.org/solo",
        "content_origin": "yuzheng-spoken-source", "yuzheng_stance_weight": "direct", "evidence_role": "primary-speech",
        "speaker_classification": "solo-yuzheng"}, "[00:00:01](https://www.youtube.com/watch?v=ABCDEFGHIJK&t=1s) 合成内容。")
    c = {"entities": [{"entity_id": "guest:alpha", "display_name": "Synthetic Alpha", "aliases": ["Alpha"]}],
        "sources": [{"source_id": "youtube-guest123456", "title": "Synthetic Alpha programme", "url": "https://www.youtube.com/watch?v=guest123456"}],
        "appearances": [{"entity_id": "guest:alpha", "source_id": "youtube-guest123456"}]}
    (context_pack / "catalog/guests.json").write_text(json.dumps(c))
    ix = ContextIndex(context_pack); ix.load()
    result = ix.retrieve("Alpha", intent="understand")
    assert result and all(p.discovery for p in result)
    assert result[0].source["excerpt"] == ""
    assert result[0].evidence["yuzheng_stance_weight"] == "not-evidence"
    counts = ix.metadata()["counts"]
    assert counts["yuzheng_solo_transcripts"] == 1


def test_positive_attribution_is_required_even_for_a_first_party_account(index):
    own = next(d for d in index.documents if d.source_type == "video-transcript")
    own.content_origin = "mixed-or-unresolved-speech"
    assert not is_yuzheng_basis(own)
    own.content_origin = "yuzheng-spoken-source"; own.data_layer = "derived"
    assert not is_yuzheng_basis(own)
    own.data_layer = "raw"; own.speaker_id = "unknown"
    assert not is_yuzheng_basis(own)
    own.speaker_id = "yuzheng"; own.speaker_status = "unresolved"
    assert not is_yuzheng_basis(own)
    own.speaker_status = "reviewed-source-attribution"
    assert is_yuzheng_basis(own)
    # A voice match counts as attribution; only his statements are a basis for his views.
    own.speaker_status = "audio-voice-match"; own.speech_act = "statement"
    assert is_yuzheng_basis(own)
    for act in ("question", "introduction", "acknowledgement", "joke", "reported-other"):
        own.speech_act = act
        assert not is_yuzheng_basis(own)
    own.speech_act = "statement"; own.speaker_status = "audio-anchor-reviewed"
    assert not is_yuzheng_basis(own)


def test_source_heading_and_archive_access_note_are_not_answer_body(index):
    own = next(d for d in index.documents if is_yuzheng_basis(d))
    own.source_type = "course-lesson"
    own.text = "# 合成课程标题\n\n> **会员课程** · 合成的授权说明。"
    assert not is_yuzheng_basis(own)
    assert not any(p.evidence["docindex"] == index.documents.index(own)
                   for p in index.retrieve("合成课程标题", intent="understand"))
    own.text += "\n\n真正的合成课程正文。"
    assert is_yuzheng_basis(own)


def test_guest_reading_cards_keep_attribution_and_deduplicate_programmes():
    from server.answers import guest_reading_result
    from server.retrieval import Passage
    def p(i, family, speaker):
        return Passage({"id": i, "title": "Synthetic programme", "url": family, "excerpt": "Synthetic exact text",
                        "speaker_id": speaker, "speaker_status": "unresolved" if speaker == "unknown" else "reviewed-source-attribution"},
                       {"source_family": family}, False)
    result=guest_reading_result([p("S7","https://example.org/a","unknown"),p("S8","https://example.org/a","unknown"),p("S9","https://example.org/b","yuzheng")])
    assert result["status"] == "sources-only" and result["sections"] == [] and result["followups"] == []
    assert [s["id"] for s in result["sources"]] == ["S1","S2"]
    assert [s["speaker_id"] for s in result["sources"]] == ["unknown","yuzheng"]


def test_named_guest_find_returns_source_cards_without_generating_views(context_pack, monkeypatch):
    from server.app import create_app
    from fastapi.testclient import TestClient
    from test_backend import events
    import httpx
    p=dialogue(context_pack)
    (context_pack/"catalog/guests.json").write_text(json.dumps({"entities":[{"entity_id":"guest:alpha","display_name":"Synthetic Alpha","aliases":["Alpha"]}],
        "sources":[{"source_id":"youtube-synthetic01","title":"Synthetic Alpha programme","url":"https://www.youtube.com/watch?v=synthetic01"}],
        "appearances":[{"entity_id":"guest:alpha","source_id":"youtube-synthetic01"}]}))
    monkeypatch.setenv("AI_BUILDER_TOKEN","synthetic-placeholder-token")
    calls=[]
    def provider(request):
        calls.append(request.url.path)
        assert not request.url.path.endswith("chat/completions")
        return httpx.Response(200,json={"data":[]})
    with TestClient(create_app(context_pack,httpx.MockTransport(provider))) as client:
        result=events(client.post("/api/ask",json={"question":"找Alpha的节目","intent":"find"}))[-1][1]
    assert result["status"] == "sources-only" and result["sources"]
    assert result["sections"] == [] and result["followups"] == []


def test_named_find_filters_unrelated_semantic_and_navigation_hits(context_pack):
    dialogue(context_pack)
    (context_pack / "catalog/guests.json").write_text(json.dumps({"entities": [{"entity_id": "guest:alpha", "display_name": "Synthetic Alpha", "aliases": ["Alpha"]}],
        "sources": [{"source_id": "youtube-synthetic01", "title": "Synthetic Alpha programme", "url": "https://www.youtube.com/watch?v=synthetic01"}],
        "appearances": [{"entity_id": "guest:alpha", "source_id": "youtube-synthetic01"}]}))
    ix = ContextIndex(context_pack); ix.load()
    outsiders = [i for i, d in enumerate(ix.documents) if "guest:alpha" not in d.participant_entity_ids.split("；")]
    ix.architecture = SimpleNamespace(route=lambda *a: ["synthetic"],
        linked_candidates=lambda ids: [{"docindex": i, "weight": 1, "focus_terms": ["职业"]} for i in outsiders])
    result = ix.retrieve("找Alpha的职业选择节目", intent="find", semantic_candidates=[{"docindex": i, "score": .99} for i in outsiders])
    assert result and all("guest:alpha" in p.source["participant_entity_ids"].split("；") for p in result)
    assert ix.participant_entities(ix.retrieval_query("那还有哪些？", [{"question": "找Alpha的节目"}])) == {"guest:alpha"}
    assert ix.participant_entities(ix.retrieval_query("工作价值", [{"question": "找Alpha的节目"}])) == set()


def test_semantic_candidates_are_cut_after_the_source_purpose(index):
    """The closest eligible passage arrives even when many ineligible ones rank above it."""
    outsiders = [i for i, d in enumerate(index.documents) if not is_yuzheng_basis(d)]
    own = next(i for i, d in enumerate(index.documents) if is_yuzheng_basis(d))
    semantic = [{"docindex": outsiders[n % len(outsiders)], "score": .95} for n in range(60)] + [{"docindex": own, "score": .5}]
    passages = index.retrieve("毫不相干的提问词汇", intent="understand", semantic_candidates=semantic)
    assert [p.evidence["docindex"] for p in passages] == [own]
