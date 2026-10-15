"""Build the store of the pinned release (server/store.py) before the service starts.

The Dockerfile runs this while it builds the image, where memory is plentiful, so the service never
holds the whole corpus: it starts from the store (ASK_STORE=prebuilt forbids building one there).

  python -m server.build_store
"""

from __future__ import annotations

import resource
import sys
import time
from pathlib import Path

from .retrieval import ContextIndex

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    started = time.monotonic()
    index = ContextIndex(ROOT / "data" / "context", require_lock=True)
    index.load()
    semantic = index.semantic_index()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    print(f"store: {len(index.briefs)} passages, semantic index {'ready' if semantic.ready else 'not ready'}, "
          f"{time.monotonic() - started:.1f}s, peak {peak:.0f} MB")
    return 0 if index.briefs else 1


if __name__ == "__main__":
    raise SystemExit(main())
