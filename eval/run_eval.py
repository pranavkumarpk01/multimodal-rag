"""
Score retrieval against the golden set.

    py eval/run_eval.py                    # score current settings
    py eval/run_eval.py --top 5            # different cut-off
    py eval/run_eval.py --compare          # dense vs bm25 vs hybrid

No LLM is called here. The expected page for every question is already known,
so scoring is deterministic, free, and takes seconds. That is the whole point:
you can change chunk size, the image filters, or CANDIDATES, re-run this, and
see a number move instead of guessing.

Metrics
  hit@k        did the right page appear in the top k?
  MRR          1/rank of the first correct hit, averaged - rewards ranking it 1st
  image-hit@k  same as hit@k but only for questions whose answer is a figure
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import retrieve, store  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden.jsonl"


def load_golden():
    if not GOLDEN.exists():
        sys.exit(f"No golden set at {GOLDEN}. Run: py eval/make_golden.py")
    with GOLDEN.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def rank_of_page(hits, expected_page):
    """1-based position of the first hit on the expected page, or None."""
    for i, hit in enumerate(hits, 1):
        if hit["payload"]["page"] == expected_page:
            return i
    return None


def score(rows, mode, top_k):
    """mode: 'hybrid' | 'dense' | 'bm25'"""
    hits, reciprocal, image_hits, image_total = 0, 0.0, 0, 0

    for row in rows:
        question = row["question"]

        if mode == "hybrid":
            texts, images = retrieve.search(
                question, top_text=top_k, top_images=top_k, candidates=40
            )
            results = texts + images
        elif mode == "dense":
            from app.llm import embed_query
            raw = store.dense_search(embed_query(question), limit=top_k * 2)
            results = [{"payload": p} for p, _s in raw]
        else:  # bm25
            raw = retrieve.bm25_search(question, limit=top_k * 2)
            results = [{"payload": p} for p, _s in raw]

        rank = rank_of_page(results, row["expect_page"])
        if rank:
            hits += 1
            reciprocal += 1.0 / rank

        if row["expect_kind"] == "image":
            image_total += 1
            if rank:
                image_hits += 1

    total = len(rows)
    return {
        "mode": mode,
        "n": total,
        f"hit@{top_k}": hits / total if total else 0.0,
        "mrr": reciprocal / total if total else 0.0,
        f"image_hit@{top_k}": image_hits / image_total if image_total else None,
        "image_questions": image_total,
    }


def show(result):
    keys = [k for k in result if k.startswith("hit@")]
    hit_key = keys[0]
    img_key = [k for k in result if k.startswith("image_hit@")][0]
    image_score = result[img_key]
    image_text = f"{image_score:.1%}" if image_score is not None else "n/a"
    print(f"  {result['mode']:<8} {hit_key} {result[hit_key]:>6.1%}   "
          f"MRR {result['mrr']:>5.3f}   images {image_text:>6} "
          f"({result['image_questions']} q)")


def main(argv):
    top_k = 5
    if "--top" in argv:
        top_k = int(argv[argv.index("--top") + 1])

    rows = load_golden()
    retrieve.load_index()

    print(f"\ngolden set: {len(rows)} questions, cut-off k={top_k}\n")

    if "--compare" in argv:
        print("  comparing retrievers (this is the argument for hybrid search)")
        for mode in ("dense", "bm25", "hybrid"):
            show(score(rows, mode, top_k))
    else:
        show(score(rows, "hybrid", top_k))

    print("\n  hit@k       right page anywhere in the top k")
    print("  MRR         1/rank of the first correct hit (1.000 = always ranked first)")
    print("  images      hit@k restricted to questions whose answer is a figure\n")


if __name__ == "__main__":
    main(sys.argv[1:])
