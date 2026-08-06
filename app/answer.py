"""
Turning retrieved chunks into an answer.

This is where the trick from enrichment reverses.

During ingestion an image was turned INTO text so it could be searched.
Here that description is used only to label the figure in the prompt - the
model is handed the REAL PNG bytes and looks at the picture itself. It can
therefore read detail the caption never mentioned.

One rule shapes the whole prompt:

    THE PROMPT MUST STAND ALONE WITHOUT THE IMAGES.

Every figure's description is written into the text. So when the chain falls
through to a text-only model (Groq), the answer is still grounded - it reads
the descriptions instead of seeing the pictures. Quality drops a little;
nothing breaks. And either way the user gets the same exact image files back,
because retrieval already decided which images are relevant.
"""

import io

from PIL import Image

from app import config, diagram, retrieve
from app.llm import ask_json
from app.models import AnswerPayload

SYSTEM_RULES = """You answer questions about a document using only the excerpts provided.

Rules:
- Use only the material below. If it does not contain the answer, say so plainly
  instead of guessing.
- Cite page numbers inline, like (page 4).
- When a figure is relevant, refer to it by its label, like [IMAGE-1].
- Some figures are attached as real images. If you can see them, describe what is
  actually in them. If you cannot, rely on the written description of each figure.
- Be concise and concrete. No preamble."""


def _load_image(path, max_px=None):
    """Read a PNG and downscale it so a page render doesn't cost a fortune."""
    max_px = max_px or config.ANSWER_IMAGE_MAX_PX
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_prompt(question, texts, images):
    """
    Assemble the prompt. Returns (prompt_text, label_map) where label_map
    maps "IMAGE-1" -> the image hit, so we can resolve what the model cited.
    """
    parts = [SYSTEM_RULES, "", "=" * 60, "TEXT EXCERPTS", "=" * 60]

    if not texts:
        parts.append("(none)")
    for i, hit in enumerate(texts, 1):
        payload = hit["payload"]
        body = payload["text"][:config.MAX_CHARS_PER_TEXT_CHUNK]
        heading = payload.get("heading") or "(no heading)"
        parts.append(f"\n[T{i}] page {payload['page']} - {heading}\n{body}")

    parts += ["", "=" * 60, "FIGURES", "=" * 60]

    label_map = {}
    if not images:
        parts.append("(none)")
    for i, hit in enumerate(images, 1):
        label = f"IMAGE-{i}"
        label_map[label] = hit
        payload = hit["payload"]
        caption = payload.get("heading") or "figure"
        parts.append(
            f"\n[{label}] page {payload['page']} - {caption}\n"
            f"Description: {payload['text'][:config.MAX_CHARS_PER_TEXT_CHUNK]}"
        )

    parts += [
        "", "=" * 60,
        f"QUESTION: {question}",
        "=" * 60,
        "",
        "Answer the question. In used_image_ids list only the figure labels you "
        "actually relied on (for example [\"IMAGE-1\"]); use an empty list if the "
        "text alone was enough.",
    ]
    return "\n".join(parts), label_map


def answer_question(question, top_text=None, top_images=None, attach_images=True,
                    allow_diagram=True):
    """
    Full query path: retrieve -> build prompt -> ask -> assemble response.

    attach_images=False forces the text-only path, which is how you check that
    the fallback still produces a sane answer.

    If the question asks for a NEW diagram to be drawn, a Graphviz diagram is
    generated from the retrieved text and returned alongside the answer under
    "diagram". Retrieved images are still returned as normal - a generated
    diagram never replaces evidence from the document, it sits beside it.
    """
    texts, images = retrieve.search(question, top_text=top_text, top_images=top_images)

    drawing = None
    if allow_diagram and diagram.wants_diagram(question):
        drawing = diagram.generate(question, texts)
    prompt, label_map = build_prompt(question, texts, images)

    image_parts = []
    if attach_images:
        for hit in images:
            path = hit["payload"].get("image_path")
            if not path:
                continue
            try:
                image_parts.append((_load_image(path), "image/png"))
            except Exception as err:
                print(f"[answer] could not load {path}: {err}")

    data, model_used = ask_json(prompt, schema=AnswerPayload, images=image_parts or None)

    cited = {label.upper().strip() for label in data.get("used_image_ids", [])}

    # Gemini honours the response schema; Groq's JSON mode only guarantees valid
    # JSON, so it sometimes omits used_pages. Backfill from the figures it did
    # cite - that page really is a source, so this is inference, not invention.
    used_pages = set(data.get("used_pages") or [])
    if not used_pages:
        used_pages = {
            hit["payload"]["page"] for label, hit in label_map.items() if label in cited
        }

    return {
        "question": question,
        "answer": data.get("answer", "").strip(),
        "model": model_used,
        "diagram": drawing,          # None unless a drawing was requested
        "intent": "draw" if drawing else "find",
        "saw_images": bool(image_parts) and model_used.startswith("gemini"),
        "used_pages": sorted(used_pages),
        "images": [
            {
                "label": label,
                "id": hit["payload"]["id"],
                "page": hit["payload"]["page"],
                "caption": hit["payload"].get("heading") or "figure",
                "path": hit["payload"].get("image_path"),
                "score": round(hit["score"], 5),
                "found_by": hit["found_by"],
                # everything retrieved is returned; this flags what the answer leaned on
                "cited_by_model": label in cited,
            }
            for label, hit in label_map.items()
        ],
        "text_sources": [
            {
                "page": hit["payload"]["page"],
                "heading": hit["payload"].get("heading", ""),
                "score": round(hit["score"], 5),
                "found_by": hit["found_by"],
            }
            for hit in texts
        ],
    }


# ----------------------------------------------------------------------
#   py -m app.answer "your question"
#   py -m app.answer --no-images "your question"     (simulate the fallback)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    attach = "--no-images" not in argv
    argv = [a for a in argv if a != "--no-images"]
    question = " ".join(argv) or "What are the six core engineering building blocks?"

    result = answer_question(question, attach_images=attach)

    print("\n" + "=" * 78)
    print("Q:", result["question"])
    print("=" * 78)
    print(result["answer"])
    print("-" * 78)
    print(f"model      : {result['model']}   (saw images: {result['saw_images']})")
    print(f"pages used : {result['used_pages']}")
    print("images returned:")
    for img in result["images"]:
        mark = "CITED" if img["cited_by_model"] else "     "
        print(f"  [{mark}] {img['label']}  p{img['page']:<3} {img['caption'][:44]}")
        print(f"           {img['path']}")
    print()
