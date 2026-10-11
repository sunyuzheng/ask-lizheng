"""People named in a question, from Open Context's shared name table."""
import json
from pathlib import Path

import pytest

from server import answers
from server.names import NameIndex
from server.retrieval import ContextIndex

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "data/context/catalog/guest-names.json"


@pytest.fixture(scope="module")
def names():
    return NameIndex(json.loads(TABLE.read_text()))


@pytest.fixture(scope="module")
def index():
    loaded = ContextIndex(ROOT / "data/context", require_lock=True)
    loaded.load()
    return loaded


@pytest.mark.parametrize("question", [
    "Sean Ellis 的增长方法", "Joe Kumar 写的那几章", "Paul Graham 怎么看创业", "Terry Tao", "Emily Oster 的书",
    "孙宇晨的观点", "陈哲远", "柯西不等式怎么理解", "Su Hua", "Anna Karenina",
])
def test_a_longer_name_is_not_its_first_name(names, question):
    assert names.resolve(question) == set()


@pytest.mark.parametrize("question, expected", [
    ("Leo Charisma", {"guest:leo-charisma"}),
    ("Leo(Charisma)", {"guest:leo-charisma"}),
    ("Leo（Charisma）", {"guest:leo-charisma"}),
    ("硅谷徐老师怎么看AI", {"guest:howie-xu"}),
    ("王臻", {"guest:wang-zhen-and-nick"}),
    ("Nick", {"guest:wang-zhen-and-nick"}),
    ("宝二爷", {"guest:chandler-guo"}),
    ("Multi-FIRE 系列", {"guest:leon", "guest:lulu"}),
    ("鸭哥怎么看AI", {"guest:ya-ge"}),
    ("卢蔚对Web3的判断", {"guest:lu-wei"}),
    ("Lulu 水贝珠宝", {"guest:lulu-shuibei"}),
    ("leon and lulu", {"guest:leon", "guest:lulu", "guest:lulu-shuibei"}),
])
def test_names_qualifiers_members_and_base_names(names, question, expected):
    assert names.resolve(question) == expected


def test_namesakes_without_a_qualifier_stay_separate(names):
    # Two different Leos stay separate. Leo & Mandy's members have their own pages, so once the
    # catalog declares the group's members their names find the people, not the group as well.
    found = names.resolve("Leo")
    assert {"guest:leo-charisma", "guest:leo-union-trading-company"} <= found <= {"guest:leo-charisma", "guest:leo-mandy", "guest:leo-union-trading-company"}


def test_key_rule_matches_the_published_examples(names):
    for text, key in json.loads(TABLE.read_text())["key_examples"]:
        assert names.key(text) == key


def test_a_reader_with_a_different_key_rule_fails_closed():
    table = json.loads(TABLE.read_text())
    with pytest.raises(ValueError):
        NameIndex({**table, "key_drop": ""})


def test_host_names_come_from_the_table(names):
    assert {"立正", "孙煜征", "Yuzheng Sun"} <= set(names.speaker_names("yuzheng"))


def test_a_reviewed_guest_statement_may_use_the_names_readers_use(index):
    passages = index.retrieve("校风 行胜于言 as an engineer build 一手的认识", limit=12, intent="find")
    target = next(p for p in passages if p.source.get("speaker_id") == "guest:lu-wei" and p.evidence.get("speech_act") == "statement")
    sid = target.source["id"]
    for name in ("卢蔚", "Cooper", "卢蔚（Cooper）"):
        answer = answers.ModelAnswer(status="answered", summary="摘要。", followups=[], clarifying_questions=[], sections=[
            answers.AnswerSection(heading="先动手再判断", body=f"{name}认为要先亲手做东西再下判断。[{sid}]", source_ids=[sid], kind="source")])
        assert not answers.misattributed(answer, passages), name
    # The host's other names are checked like 立正.
    answer = answers.ModelAnswer(status="answered", summary="摘要。", followups=[], clarifying_questions=[], sections=[
        answers.AnswerSection(heading="先动手", body=f"孙煜征认为要先亲手做东西。[{sid}]", source_ids=[sid], kind="source")])
    assert answers.misattributed(answer, passages)


def test_named_find_says_who_it_found():
    passage = answers.Passage({"id": "S1", "title": "节目", "url": "https://www.youtube.com/watch?v=AAAAAAAAAAA"},
                              {"source_family": "https://www.youtube.com/watch?v=AAAAAAAAAAA"}, False)
    assert "2位嘉宾" in answers.guest_reading_result([passage], ["Leo（Charisma）", "Leo（The Union Trading Company）"])["summary"]
    assert answers.guest_reading_result([passage], ["卢蔚（Cooper）"])["summary"] == "找到了卢蔚（Cooper）的节目与原文片段。"
