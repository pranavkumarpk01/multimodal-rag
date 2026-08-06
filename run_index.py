"""
Load ingested chunks into Qdrant.

    py run_index.py              # index every manifest in artifacts/
    py run_index.py --recreate   # wipe the collection first

Ingestion and indexing are deliberately separate: you can rebuild the index
without re-parsing PDFs or re-spending vision quota.
"""

import sys

from app import config, store


def main(argv):
    recreate = "--recreate" in argv

    chunks = store.load_all_manifests()
    if not chunks:
        sys.exit(f"No manifests found in {config.ARTIFACT_DIR}. Run run_ingest.py first.")

    store.ensure_collection(recreate=recreate)
    if recreate:
        print("[index] collection recreated (was wiped)")

    store.index_chunks(chunks)

    kinds = {}
    for chunk in chunks:
        kinds[chunk.kind] = kinds.get(chunk.kind, 0) + 1

    print(f"\ncollection '{config.QDRANT_COLLECTION}' now holds {store.count()} points")
    print("  " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    print(f"\ndashboard: {config.QDRANT_URL}/dashboard\n")


if __name__ == "__main__":
    main(sys.argv[1:])
