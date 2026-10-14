#!/usr/bin/env python3
"""Lay out every public material as a light on the answer map (data/answer-map.json).

The map is what a reader watches while an answer is found: the question at the centre, the
judgment cards on the ring around it, and around them every material the answers draw from,
one light each. Distance from the centre is when it was published (2020 inside, the latest
year outside); the angle is its topic, so a question lights up the part of the map it is about.

Topics come from config/answer-map-topics.json: each names words that pick a handful of seed
materials. Their mean embeddings start a few rounds of k-means over the materials that are
not comments (comments share a format that would otherwise pull them together), and every
material, comments included, then joins the nearest topic. Within a topic, materials are
ordered along their first principal direction, so related ones sit side by side. The GDAP book
chapters and Statsig posts have no embeddings and belong to growth and data.

scripts/update_context.py runs this after each context update, in a child process like every
step that runs Open Context's parser, and the service serves the result at /api/answer-map.
Plain Python, so it needs nothing the service does not. By hand:
    python3 scripts/build_answer_map.py
"""
from __future__ import annotations

import json
import math
import random
import re
import sys
from array import array
from collections import defaultdict
from datetime import date
from operator import mul
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.retrieval import ContextIndex  # noqa: E402

CONTEXT = ROOT / "data" / "context"
SEMANTIC = ROOT / "data" / "semantic"
TOPICS = ROOT / "config" / "answer-map-topics.json"
OUTPUT = ROOT / "data" / "answer-map.json"

# What a light is, by the folder it lives in. English versions repeat their originals and are
# matched to them by link in the page, so they get no light of their own.
KINDS = {
    "videos": "v", "dialogues": "d", "community-posts": "w", "blog-posts": "w",
    "community-comments": "r", "course-lessons": "c", "book-chapters": "b",
    "conversation-excerpts": "x",
}
SKIP = {"english-translations", "english-community"}
FIRST_YEAR = 2020
INNER, OUTER = 0.26, 1.0     # radius of the first and last moment, as a share of the map
CARD_RING = 0.19
GAP = 0.012                  # turns left empty between topics
ROUNDS = 4
SEED = 20261013


def material_key(path: str) -> str:
    """corpus/videos/20250211-Y20_JOyMs3k.md -> videos/20250211-Y20_JOyMs3k (the service sends the same key)."""
    return re.sub(r"\.(md|json)$", "", path.removeprefix("corpus/"))


def when(value: str) -> float:
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not match:
        return float("nan")
    y, m, d = map(int, match.groups())
    start = date(y, 1, 1).toordinal()
    return y + (date(y, m, d).toordinal() - start) / (date(y + 1, 1, 1).toordinal() - start)


def days(value: str) -> int:
    """Days from the first year's first day to the date, for the timeline (before it: 0)."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not match:
        return -1
    return max(0, date(*map(int, match.groups())).toordinal() - date(FIRST_YEAR, 1, 1).toordinal())


def dot(a, b) -> float:
    return sum(map(mul, a, b))


def unit(v: list[float]) -> list[float]:
    norm = math.sqrt(dot(v, v)) or 1.0
    return [x / norm for x in v]


def mean(vectors) -> list[float]:
    vectors = list(vectors)
    total = [0.0] * len(vectors[0])
    for v in vectors:
        total = [t + x for t, x in zip(total, v)]
    return [t / len(vectors) for t in total]


def nearest(v, centroids) -> int:
    scores = [dot(v, c) for c in centroids]
    return scores.index(max(scores))


def principal(vectors: list[list[float]], rounds: int = 40) -> list[float]:
    """First principal direction of the vectors, by power iteration from the longest centred one."""
    centre = mean(vectors)
    rows = [[x - c for x, c in zip(v, centre)] for v in vectors]
    direction = unit(max(rows, key=lambda r: dot(r, r)))
    for _ in range(rounds):
        step = [0.0] * len(direction)
        for r in rows:
            s = dot(r, direction)
            step = [a + s * x for a, x in zip(step, r)]
        direction = unit(step)
    return direction


def load_materials() -> list[dict]:
    index = ContextIndex(CONTEXT)
    index.load()
    materials: dict[str, dict] = {}
    for doc in index.documents:
        parts = doc.path.split("/")
        if len(parts) < 3 or parts[0] != "corpus" or parts[1] in SKIP or parts[1] not in KINDS:
            continue
        key = material_key(doc.path)
        published = doc.original_published_at or doc.published_at
        moment = when(published)
        row = materials.setdefault(key, {"key": key, "path": doc.path, "kind": KINDS[parts[1]], "title": doc.title, "when": moment,
                                         "days": days(published)})
        if math.isnan(row["when"]) and not math.isnan(moment):
            row["when"] = moment
            row["days"] = days(published)
    rows = [materials[k] for k in sorted(materials)]
    missing = [r["key"] for r in rows if math.isnan(r["when"])]
    if missing:
        raise SystemExit(f"{len(missing)} materials have no date, e.g. {missing[:3]}")
    return rows


def load_embeddings(rows: list[dict]) -> list[list[float] | None]:
    """Each material's mean window embedding, or None when it has no windows."""
    semantic = json.loads((SEMANTIC / "index.json").read_text())
    dimensions = semantic["dimensions"]
    vectors = array("f")
    vectors.frombytes((SEMANTIC / semantic["vector_file"]).read_bytes())
    if sys.byteorder != "little":
        vectors.byteswap()
    by_key = defaultdict(list)
    for position, entry in enumerate(semantic["entries"]):
        by_key[material_key(entry["path"])].append(position)
    embeddings = []
    for row in rows:
        positions = by_key.get(row["key"])
        embeddings.append(unit(mean(vectors[p * dimensions:(p + 1) * dimensions] for p in positions)) if positions else None)
    return embeddings


def main() -> None:
    rows = load_materials()
    embedding = load_embeddings(rows)
    has = [v is not None for v in embedding]

    config = json.loads(TOPICS.read_text())
    topics = config["topics"]
    order = {t["key"]: n for n, t in enumerate(topics)}
    fixed = {kind: order[t["key"]] for t in topics for kind in t.get("kinds", [])}
    centroids = []
    for topic in topics:
        pattern = re.compile(topic["seed"])
        seeds = [i for i, row in enumerate(rows) if has[i] and row["kind"] != "r" and pattern.search(row["title"])]
        if len(seeds) < 3:
            raise SystemExit(f"Topic {topic['key']} found only {len(seeds)} seed materials")
        centroids.append(unit(mean(embedding[i] for i in seeds)))
    core = [i for i in range(len(rows)) if has[i] and rows[i]["kind"] != "r"]
    for _ in range(ROUNDS):
        groups = defaultdict(list)
        for i in core:
            groups[nearest(embedding[i], centroids)].append(i)
        for t, members in groups.items():
            centroids[t] = unit(mean(embedding[i] for i in members))
    topic_of = []
    for i, row in enumerate(rows):
        if has[i]:
            topic_of.append(nearest(embedding[i], centroids))
        else:
            topic_of.append(fixed.get(row["path"].split("/")[1], order[config["fallback"]]))

    # Angular span grows with the topic's size, but slowly, so small topics stay readable.
    counts = [topic_of.count(t) for t in range(len(topics))]
    weights = [c ** 0.75 for c in counts]
    spans = [(1 - GAP * len(topics)) * w / sum(weights) for w in weights]
    last = max(FIRST_YEAR + 1, max(math.floor(row["when"]) + 1 for row in rows))
    rng = random.Random(SEED)
    turn = [0.0] * len(rows)
    radius = [0.0] * len(rows)
    ranges = []
    start = 0.0
    for t in range(len(topics)):
        members = [i for i in range(len(rows)) if topic_of[i] == t]
        a0 = start
        ranges.append((a0, a0 + spans[t]))
        start = a0 + spans[t] + GAP
        embedded = [i for i in members if has[i]]
        score = {}
        if len(embedded) > 2:
            direction = principal([embedding[i] for i in embedded])
            score = {i: dot(embedding[i], direction) for i in embedded}
        jitter = {i: rng.random() - 0.5 for i in members}
        ranked = sorted(members, key=lambda i: (score.get(i, jitter[i]), rows[i]["key"]))
        for place, i in enumerate(ranked):
            share = (place + 0.5) / len(ranked)
            turn[i] = a0 + spans[t] * (0.04 + 0.92 * share) + rng.gauss(0, spans[t] * 0.012)
            moment = (rows[i]["when"] - FIRST_YEAR) / (last - FIRST_YEAR)
            radius[i] = INNER + (OUTER - INNER) * min(1, max(0, moment)) + rng.gauss(0, 0.008)

    # Judgment cards sit on their ring at the mean angle of the materials they rest on.
    cards_json = json.loads((CONTEXT / "context" / "decision-cards.json").read_text())["cards"]
    position = {row["key"]: i for i, row in enumerate(rows)}
    cards = []
    for card in cards_json:
        sources = [position[material_key(s["path"])] for s in card["sources"] if material_key(s["path"]) in position]
        angle = 0.0
        if sources:
            x = sum(math.cos(turn[i] * 2 * math.pi) for i in sources)
            y = sum(math.sin(turn[i] * 2 * math.pi) for i in sources)
            angle = math.atan2(y, x) / (2 * math.pi) % 1
        cards.append({"id": card["id"], "title": card["title"], "t": angle, "src": sources})
    # Keep cards apart on their ring: at least a little more than their own width.
    cards.sort(key=lambda c: c["t"])
    least = 1 / (len(cards) * 1.6)
    for _ in range(200):
        moved = False
        for a, b in zip(cards, cards[1:] + cards[:1]):
            gap = (b["t"] - a["t"]) % 1
            if gap < least:
                push = (least - gap) / 2
                a["t"] = (a["t"] - push) % 1
                b["t"] = (b["t"] + push) % 1
                moved = True
        if not moved:
            break
        cards.sort(key=lambda c: c["t"])

    lock = json.loads((ROOT / "data" / "context-lock.json").read_text())
    output = {
        "v": 1,
        "release": lock.get("source_commit", ""),
        "years": [FIRST_YEAR, last],
        "radius": [INNER, OUTER, CARD_RING],
        "topics": [{"k": t["key"], "l": t["label"], "a": [round(a0, 4), round(a1, 4)], "n": counts[n]}
                   for n, (t, (a0, a1)) in enumerate(zip(topics, ranges))],
        "kinds": "".join(row["kind"] for row in rows),
        "t": [round(x % 1 * 10000) for x in turn],
        "r": [round(x * 1000) for x in radius],
        "g": topic_of,
        # The timeline's places: each material's own date, in days since the first year began.
        "d": [row["days"] for row in rows],
        "keys": [row["key"] for row in rows],
        "cards": [{"id": c["id"], "title": c["title"], "t": round(c["t"] * 10000), "src": c["src"]} for c in cards],
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{OUTPUT.relative_to(ROOT)}: {len(rows)} materials, {len(cards)} cards, {OUTPUT.stat().st_size // 1024} KB")
    for n, t in enumerate(topics):
        members = [i for i in range(len(rows)) if topic_of[i] == n and has[i]]
        sample = sorted(members, key=lambda i: -dot(embedding[i], centroids[n]))[:6]
        print(f"  {t['label']} ({counts[n]}): " + " / ".join(rows[i]["title"][:22] for i in sample))


if __name__ == "__main__":
    main()
