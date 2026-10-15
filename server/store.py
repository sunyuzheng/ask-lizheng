"""The public corpus as a file the service reads on demand, so its memory stays small as the corpus grows.

ContextIndex used to hold every passage in memory twice (as published, and lowercased for search)
with all its metadata, and found candidates by scanning every passage for each word of a question:
about 200 MB in 2026-10, growing with every passage, in a 256 MB container. The store keeps the
passages and a word index in one SQLite file, built from the pinned release (when the image is
built, or on first load elsewhere): which passages contain each Chinese character and character
pair and each Latin word, how often, and whether in the title. A question reads the postings of its
own words and only the passages it finally cites.

Counts are the counts scanning gave. A character pair occurs only inside a run of Chinese characters
and a Latin term only inside a run of word characters, so str.count over the passage equals the sum
of str.count over those runs: pairs are counted per run, and a Latin term's count is the sum, over
the passage's word tokens that contain it, of the token's count times the term's count inside it.
Any other kind of term is counted by scanning the stored lowercased texts.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import zlib
from array import array
from collections import Counter, OrderedDict
from pathlib import Path

SCHEMA = 1
HAN_RUN = re.compile(r"[㐀-鿿]+")
WORD_RUN = re.compile(r"[a-z0-9_+.-]+")
HAN_TERM = re.compile(r"[㐀-鿿]{1,2}")
# A query's Latin terms (Open Context's WORD on lowercased text) are made of these characters only.
WORD_TERM = re.compile(r"[a-z0-9_+.-]+")
CACHE_TERMS = 256


def _pack(values) -> bytes:
    return zlib.compress(array("I", values).tobytes(), 6)


def _unpack(blob: bytes) -> array:
    values = array("I")
    values.frombytes(zlib.decompress(blob))
    return values


def han_counts(text: str) -> Counter:
    """Each Chinese character and character pair in the text, counted as str.count counts them."""
    counts: Counter = Counter()
    for run in HAN_RUN.findall(text):
        for term in set(run):
            counts[term] += run.count(term)
        for term in {run[i:i + 2] for i in range(len(run) - 1)}:
            counts[term] += run.count(term)
    return counts


def han_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for run in HAN_RUN.findall(text):
        terms.update(run)
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def build(path: Path | None, rows, meta: dict) -> "Store":
    """Write a store and open it. rows yields (document fields, brief, lowercased body, lowercased
    title) in passage order; meta is kept as given. Without a path the store lives in memory."""
    if path is None:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        _write(connection, rows, meta)
        return Store(connection)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".partial")
    os.close(handle)
    try:
        connection = sqlite3.connect(temporary)
        _write(connection, rows, meta)
        connection.close()
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return Store.open(path)


def _write(connection: sqlite3.Connection, rows, meta: dict) -> None:
    connection.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE docs (i INTEGER PRIMARY KEY, path TEXT NOT NULL, id TEXT NOT NULL, source_id TEXT NOT NULL, data BLOB NOT NULL);
        CREATE TABLE texts (i INTEGER PRIMARY KEY, body BLOB NOT NULL, title BLOB NOT NULL);
        CREATE TABLE terms (term TEXT PRIMARY KEY, kind TEXT NOT NULL, ids BLOB NOT NULL, counts BLOB NOT NULL, titles BLOB NOT NULL) WITHOUT ROWID;
    """)
    body_ids: dict[str, array] = {}
    body_counts: dict[str, array] = {}
    title_ids: dict[str, array] = {}
    briefs, total_length = [], 0
    for i, (fields, brief, body, title) in enumerate(rows):
        connection.execute("INSERT INTO docs VALUES (?, ?, ?, ?, ?)", (i, fields["path"], fields["id"], fields["source_id"],
                           zlib.compress(json.dumps(fields, ensure_ascii=False).encode("utf-8"), 6)))
        connection.execute("INSERT INTO texts VALUES (?, ?, ?)", (i, zlib.compress(body.encode("utf-8"), 6), zlib.compress(title.encode("utf-8"), 6)))
        briefs.append([*brief, len(body)])
        total_length += len(body)
        for term, count in (*han_counts(body).items(), *Counter(WORD_RUN.findall(body)).items()):
            body_ids.setdefault(term, array("I")).append(i)
            body_counts.setdefault(term, array("I")).append(count)
        for term in han_terms(title) | set(WORD_RUN.findall(title)):
            title_ids.setdefault(term, array("I")).append(i)
    empty = _pack(())
    for term in body_ids.keys() | title_ids.keys():
        kind = "h" if HAN_TERM.fullmatch(term) else "w"
        connection.execute("INSERT INTO terms VALUES (?, ?, ?, ?, ?)", (term, kind,
            _pack(body_ids.get(term, ())) if term in body_ids else empty,
            _pack(body_counts.get(term, ())) if term in body_counts else empty,
            _pack(title_ids.get(term, ())) if term in title_ids else empty))
    connection.execute("CREATE INDEX docs_place ON docs(path, id)")
    connection.execute("CREATE INDEX docs_source ON docs(source_id, i)")
    connection.execute("CREATE INDEX terms_kind ON terms(kind, term)")
    values = {**meta, "schema": SCHEMA, "count": len(briefs), "total_length": total_length}
    connection.executemany("INSERT INTO meta VALUES (?, ?)", [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()])
    connection.execute("INSERT INTO meta VALUES ('briefs', ?)", (json.dumps(briefs, ensure_ascii=False),))
    connection.commit()


class Store:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.execute("PRAGMA cache_size=-4096")  # at most 4 MB of pages held by SQLite
        self._cache: OrderedDict[str, tuple[array, array, array]] = OrderedDict()

    @classmethod
    def open(cls, path: Path) -> "Store":
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
        return cls(connection)

    def meta(self, key: str, default=None):
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def briefs(self) -> list:
        return self.meta("briefs", [])

    def fields(self, i: int) -> dict:
        row = self.connection.execute("SELECT data FROM docs WHERE i = ?", (i,)).fetchone()
        if row is None:
            raise IndexError(i)
        return json.loads(zlib.decompress(row[0]))

    def all_fields(self):
        for (data,) in self.connection.execute("SELECT data FROM docs ORDER BY i"):
            yield json.loads(zlib.decompress(data))

    def find(self, path: str, document_id: str) -> int | None:
        row = self.connection.execute("SELECT i FROM docs WHERE path = ? AND id = ? ORDER BY i LIMIT 1", (path, document_id)).fetchone()
        return row[0] if row else None

    def first_with_source(self, source_id: str) -> int | None:
        row = self.connection.execute("SELECT i FROM docs WHERE source_id = ? ORDER BY i LIMIT 1", (source_id,)).fetchone()
        return row[0] if row else None

    def postings(self, term: str) -> tuple[array, array, array]:
        """The passages whose body contains the term (ascending), how often, and those with it in the title."""
        cached = self._cache.get(term)
        if cached is not None:
            self._cache.move_to_end(term)
            return cached
        if HAN_TERM.fullmatch(term):
            row = self.connection.execute("SELECT ids, counts, titles FROM terms WHERE term = ?", (term,)).fetchone()
            value = tuple(map(_unpack, row)) if row else (array("I"), array("I"), array("I"))
        elif WORD_TERM.fullmatch(term):
            value = self._containing(term)
        else:
            value = self._scanned(term)
        self._cache[term] = value
        if len(self._cache) > CACHE_TERMS:
            self._cache.popitem(last=False)
        return value

    def _containing(self, term: str) -> tuple[array, array, array]:
        counts: Counter = Counter()
        titled: set[int] = set()
        rows = self.connection.execute("SELECT term, ids, counts, titles FROM terms WHERE kind = 'w' AND instr(term, ?) > 0", (term,))
        for token, ids, numbers, titles in rows:
            times = token.count(term)
            for i, n in zip(_unpack(ids), _unpack(numbers)):
                counts[i] += n * times
            titled.update(_unpack(titles))
        ids = sorted(counts)
        return array("I", ids), array("I", (counts[i] for i in ids)), array("I", sorted(titled))

    def _scanned(self, term: str) -> tuple[array, array, array]:
        ids, numbers, titled = array("I"), array("I"), array("I")
        for i, body, title in self.connection.execute("SELECT i, body, title FROM texts ORDER BY i"):
            body, title = zlib.decompress(body).decode("utf-8"), zlib.decompress(title).decode("utf-8")
            if term in body:
                ids.append(i)
                numbers.append(body.count(term))
            if term in title:
                titled.append(i)
        return ids, numbers, titled
