"""
Finding the right chunks for a question.

Two searches run over the same collection:

  DENSE   embedding similarity  - finds MEANING
          "renew credentials" matches "refresh the access token"

  BM25    keyword matching      - finds EXACT STRINGS
          "ERR_TOKEN_EXPIRED", "Figure 4.2", "issue_refund"

Neither is enough alone. Technical documents are full of identifiers that
dense search blurs, and full of paraphrase that keyword search misses.
Their two ranked lists are merged with RRF, then a quota guarantees images
get seats at the table instead of being crowded out by text.
"""

import re

from rank_bm25 import BM25Okapi

from app import config, store
from app.llm import embed_query

RRF_K = 60  # standard damping constant; higher = flatter weighting by rank

_bm25 = None
_payloads = None


# ----------------------------------------------------------------------
# BM25 index (built once, in memory, from what is already in Qdrant)
# ----------------------------------------------------------------------
def _tokenise(text):
    return [t for t in re.findall(r"[a-z0-9_]+", (text or "").lower()) if len(t) > 1]


def load_index(force=False):
    """
    Build the keyword index. Called automatically on first search.

    Rebuilds itself when the collection has grown. The BM25 index lives in
    process memory, so without this check a document indexed by another
    process (run_index.py, or a background ingest) is found by dense search
    but invisible to keyword search - a silent half-failure that is very
    hard to spot from the outside.
    """
    global _bm25, _payloads
    if _bm25 is not None and not force:
        try:
            if store.count() == len(_payloads):
                return
            print(f"[retrieve] collection changed "
                  f"({len(_payloads)} -> {store.count()}), rebuilding BM25")
        except Exception:
            return  # Qdrant unreachable; keep serving with what we have

    _payloads = store.all_payloads()
    if not _payloads:
        raise RuntimeError(
            "Nothing indexed yet. Run:  py run_index.py"
        )
    corpus = [_tokenise(f"{p.get('heading', '')} {p.get('text', '')}") for p in _payloads]
    _bm25 = BM25Okapi(corpus)
    print(f"[retrieve] BM25 index built over {len(_payloads)} chunks")


def bm25_search(query, limit):
    load_index()
    scores = _bm25.get_scores(_tokenise(query))
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [(_payloads[i], float(scores[i])) for i in ranked[:limit] if scores[i] > 0]


# ----------------------------------------------------------------------
# fusion
# ----------------------------------------------------------------------
def reciprocal_rank_fusion(*ranked_lists):
    """
    Merge ranked lists by position, not by score.

    Raw scores from a vector search and from BM25 are on different scales
    and cannot be compared. Ranks can. An item near the top of both lists
    beats an item that only one search liked.
    """
    merged = {}
    for results in ranked_lists:
        for rank, (payload, _score) in enumerate(results):
            key = payload["id"]
            entry = merged.setdefault(key, {"payload": payload, "score": 0.0, "found_by": []})
            entry["score"] += 1.0 / (RRF_K + rank + 1)
    return sorted(merged.values(), key=lambda e: e["score"], reverse=True)


# ----------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------
def search(query, top_text=None, top_images=None, candidates=None):
    """
    Returns (text_hits, image_hits) - each a list of
    {"payload": {...}, "score": float, "found_by": [...]}.
    """
    top_text = top_text or config.TOP_TEXT
    top_images = top_images or config.TOP_IMAGES
    candidates = candidates or config.CANDIDATES

    load_index()

    dense = store.dense_search(embed_query(query), limit=candidates)
    sparse = bm25_search(query, limit=candidates)

    # remember which search found what, for debugging and for the UI
    dense_ids = {p["id"] for p, _ in dense}
    sparse_ids = {p["id"] for p, _ in sparse}

    fused = reciprocal_rank_fusion(dense, sparse)
    for entry in fused:
        cid = entry["payload"]["id"]
        entry["found_by"] = (
            (["dense"] if cid in dense_ids else []) +
            (["bm25"] if cid in sparse_ids else [])
        )

    # The modality quota: take the best N of each kind separately, so text
    # chunks cannot crowd images out of the result entirely.
    texts = [e for e in fused if e["payload"]["kind"] == "text"][:top_text]
    images = [e for e in fused if e["payload"]["kind"] == "image"][:top_images]
    return texts, images


# ----------------------------------------------------------------------
# quick manual check:  py -m app.retrieve "your question"
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    question = " ".join(sys.argv[1:]) or "How should tools be defined so they fail predictably?"
    print(f"\nQUERY: {question}\n" + "=" * 78)

    texts, images = search(question)

    print(f"\nTEXT ({len(texts)})")
    for entry in texts:
        p = entry["payload"]
        print(f"  p{p['page']:<3} rrf={entry['score']:.4f}  {'+'.join(entry['found_by']):<11} "
              f"{p['heading'][:44]}")
        print(f"       {p['text'][:110].strip()}...")

    print(f"\nIMAGES ({len(images)})")
    for entry in images:
        p = entry["payload"]
        print(f"  p{p['page']:<3} rrf={entry['score']:.4f}  {'+'.join(entry['found_by']):<11} "
              f"{p['heading'][:44]}")
        print(f"       -> {p['image_path']}")
    print()
