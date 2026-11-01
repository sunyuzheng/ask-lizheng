"""The same immutable store serves simultaneous retrieval workers."""
from concurrent.futures import ThreadPoolExecutor
from test_backend import context_pack, index


def test_concurrent_fields_postings_and_source_windows_keep_their_identity(index):
    expected = [index.store.fields(i) for i in range(min(5,len(index.documents)))]
    def read(n):
        i=n % len(expected)
        row=index.store.fields(i)
        assert (row["id"],row["source_id"],row["text"]) == (expected[i]["id"],expected[i]["source_id"],expected[i]["text"])
        assert all(fields["source_id"] == row["source_id"] for _,fields in index.store.source_fields(row["source_id"]))
        index.store.postings("能力")
        assert index.store.find(row["path"],row["id"]) == i
    with ThreadPoolExecutor(max_workers=3) as workers:
        list(workers.map(read,range(120)))
