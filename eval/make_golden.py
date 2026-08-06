"""
Build a golden question set from what is already indexed.

    py eval/make_golden.py              # ~40 questions
    py eval/make_golden.py 60           # a bigger set

For each sampled chunk the model writes a question that chunk answers. The
chunk's page is the expected answer, so scoring needs no LLM at all later.

HONEST LIMITATION: auto-generated questions are easier than real ones. The
model sees the chunk, so its wording leaks in and BM25 finds it too easily.
Treat this file as a STARTING POINT - open eval/golden.jsonl and rewrite the
questions in your own words. Hand-edited goldens are worth far more than
generated ones; this just saves you the blank page.
"""

import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from app import config, store  # noqa: E402
from app.llm import ask_json  # noqa: E402

OUT = Path(__file__).resolve().parent / "golden.jsonl"

PROMPT = """Write ONE question that this document excerpt answers.

Rules:
- Ask it the way a real user would, in their own words.
- Do NOT quote distinctive phrases from the excerpt - paraphrase instead.
  (If you copy its wording, the question becomes trivially easy to match.)
- It must be answerable from this excerpt alone.
- One sentence.

Excerpt (page {page}, section "{heading}"):
{text}"""


class Question(BaseModel):
    question: str = Field(description="A single natural question, in a user's own words")


def main(argv):
    wanted = int(argv[0]) if argv else 40

    payloads = store.all_payloads()
    if not payloads:
        sys.exit("Nothing indexed. Run run_index.py first.")

    # Sample across both modalities so images get coverage too.
    texts = [p for p in payloads if p["kind"] == "text"]
    images = [p for p in payloads if p["kind"] == "image"]

    image_share = min(len(images), max(6, wanted // 4))
    text_share = min(len(texts), wanted - image_share)

    random.seed(7)  # reproducible sample
    sample = random.sample(texts, text_share) + random.sample(images, image_share)
    random.shuffle(sample)

    print(f"generating {len(sample)} questions "
          f"({text_share} text, {image_share} image)...")

    rows = []
    for i, payload in enumerate(sample, 1):
        prompt = PROMPT.format(
            page=payload["page"],
            heading=payload.get("heading") or "(none)",
            text=payload["text"][:2000],
        )
        try:
            data, _model = ask_json(prompt, schema=Question)
        except Exception as err:
            print(f"  {i:>3}. skipped ({err})")
            continue

        question = (data.get("question") or "").strip()
        if not question:
            continue

        rows.append({
            "question": question,
            "expect_page": payload["page"],
            "expect_chunk_id": payload["id"],
            "expect_kind": payload["kind"],
            "doc_id": payload["doc_id"],
        })
        print(f"  {i:>3}. [{payload['kind']:<5} p{payload['page']:<3}] {question[:70]}")
        time.sleep(config.GEMINI_SLEEP_SECONDS)

    with OUT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\nwrote {len(rows)} questions -> {OUT}")
    print("Now open it and rewrite the questions in your own words - that is "
          "where the real value comes from.")


if __name__ == "__main__":
    main(sys.argv[1:])
