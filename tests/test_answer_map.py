"""The answer map: its layout is the current context's, and the service serves it."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

import server.app as app_module
from server.app import create_app
from test_backend import context_pack  # noqa: F401  (fixture)

PROJECT = Path(__file__).resolve().parents[1]
MAP = PROJECT / "data" / "answer-map.json"


def builder():
    spec = importlib.util.spec_from_file_location("build_answer_map", PROJECT / "scripts" / "build_answer_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_map_lays_out_the_current_context():
    """Rebuilt with every context update (scripts/update_context.py); a stale map fails here."""
    layout = json.loads(MAP.read_text())
    lock = json.loads((PROJECT / "data" / "context-lock.json").read_text())
    assert layout["release"] == lock["source_commit"]
    count = len(layout["keys"])
    assert len(layout["kinds"]) == len(layout["t"]) == len(layout["r"]) == len(layout["g"]) == len(layout["d"]) == count
    # The timeline places each material on its own day, within the years the layout names.
    first, last = layout["years"]
    assert all(0 <= d <= (last - first) * 366 for d in layout["d"])
    assert layout["keys"] == sorted(set(layout["keys"]))
    # Every material the answers can cite has its light, and every light is a material.
    assert layout["keys"] == [row["key"] for row in builder().load_materials()]
    topics = json.loads((PROJECT / "config" / "answer-map-topics.json").read_text())["topics"]
    assert [topic["k"] for topic in layout["topics"]] == [topic["key"] for topic in topics]
    assert sum(topic["n"] for topic in layout["topics"]) == count
    assert all(0 <= g < len(topics) for g in layout["g"]) and all(0 <= t < 10000 for t in layout["t"])
    cards = json.loads((PROJECT / "data" / "context" / "context" / "decision-cards.json").read_text())["cards"]
    assert sorted(card["id"] for card in layout["cards"]) == sorted(card["id"] for card in cards)
    assert all(0 <= i < count for card in layout["cards"] for i in card["src"])


def test_early_talk_about_ai_is_on_the_ai_rows():
    """2026-10-15: what Yuzheng said about AI before 2024 shows with AI (early_ai in the config)."""
    config = json.loads((PROJECT / "config" / "answer-map-topics.json").read_text())
    rule, keys = config["early_ai"], [topic["key"] for topic in config["topics"]]
    ai_rows = {keys.index(rule["topic"]), *(keys.index(key) for key in rule["keep"])}
    module = builder()
    layout = json.loads(MAP.read_text())
    early = [n for n, row in enumerate(module.load_materials())
             if row["days"] < module.days(rule["before"]) and re.search(rule["title"], row["title"])]
    assert len(early) >= 20
    assert all(layout["g"][n] in ai_rows for n in early)


def test_the_early_ai_rule_moves_only_what_is_about_ai():
    module = builder()
    config = {"topics": [{"key": "ai"}, {"key": "build"}, {"key": "data"}],
              "early_ai": {"topic": "ai", "keep": ["build"], "before": "2024-01-01", "title": "机器学习|算法",
                           "text": "AI|机器学习", "text_min": 6, "text_per_10k": 10}}
    filler = "数据" * 10000
    rows = [
        {"days": 400, "title": "数据科学家真的适合机器学习吗？", "text": ""},        # title says so
        {"days": 400, "title": "一次长访谈", "text": "AI 机器学习 " * 20 + "其他" * 200},  # mostly about AI
        {"days": 400, "title": "一次长访谈", "text": "AI " * 7 + filler},             # mentions it in passing
        {"days": 2000, "title": "机器学习的新进展", "text": ""},                      # 2025: not early
        {"days": 400, "title": "算法工程师用 Cursor", "text": ""},                    # already in build
    ]
    topic_of = [2, 2, 2, 2, 1]
    assert module.early_ai(rows, topic_of, config) == [0, 1]
    assert topic_of == [0, 0, 2, 2, 1]


def test_the_service_serves_the_map_beside_its_context(context_pack, tmp_path, monkeypatch):  # noqa: F811
    stored = tmp_path / "stored.json"
    stored.write_bytes(json.dumps({"v": 1, "keys": ["videos/20250211-Y20_JOyMs3k"]}).encode())
    asked = []
    read = app_module.read_answer_map
    monkeypatch.setattr(app_module, "read_answer_map", lambda path: asked.append(path) or read(stored))
    with TestClient(create_app(context_pack)) as client:
        packed = client.get("/api/answer-map", headers={"Accept-Encoding": "gzip"})
        plain = client.get("/api/answer-map", headers={"Accept-Encoding": "identity"})
    assert asked == [context_pack.parent / "answer-map.json"]
    assert packed.status_code == plain.status_code == 200
    assert packed.json() == plain.json() == {"v": 1, "keys": ["videos/20250211-Y20_JOyMs3k"]}
    assert packed.headers["content-encoding"] == "gzip" and "s-maxage=3600" in packed.headers["cache-control"]
    assert "stale-while-revalidate" in plain.headers["cache-control"] and plain.headers["vary"] == "Accept-Encoding"
    assert plain.headers.get("content-encoding") is None and plain.content == stored.read_bytes()


def test_without_a_map_the_service_says_so(context_pack, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app_module, "read_answer_map", lambda path: None)
    with TestClient(create_app(context_pack)) as client:
        response = client.get("/api/answer-map")
    assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
