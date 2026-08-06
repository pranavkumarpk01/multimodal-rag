"""
Ingest a PDF into artifacts/ (no vector store yet - that is the next phase).

    py run_ingest.py                       # every PDF in data/
    py run_ingest.py data/MyDoc.pdf        # one file

Re-running is cheap: every vision call is cached in
artifacts/<doc_id>/enriched.json, so nothing is paid for twice.
"""

import sys
from pathlib import Path

from app import config
from app.ingest import ingest_pdf


def main(argv):
    if argv:
        targets = [Path(a) for a in argv]
    else:
        targets = sorted(config.DATA_DIR.glob("*.pdf"))

    if not targets:
        sys.exit(f"No PDFs found in {config.DATA_DIR}")

    for path in targets:
        if not path.exists():
            print(f"skipping {path} - not found")
            continue
        result = ingest_pdf(path)

        kinds = {}
        for chunk in result.chunks:
            kinds[chunk.kind] = kinds.get(chunk.kind, 0) + 1
        print(f"summary for {result.doc_id}: " +
              ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))


if __name__ == "__main__":
    main(sys.argv[1:])
