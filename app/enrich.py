"""
Turning pictures into searchable text.

This is the step that makes the whole thing work: a PNG cannot be searched,
so we ask a vision model to write text about it. That text is what gets
embedded and matched; the PNG itself is just returned at the end.

Two jobs:

  transcribe_page()  - for pages with no text layer. Reads the WHOLE page
                       and returns a full transcription plus a list of the
                       figures on it.
  describe_image()   - for a single figure pulled out of a normal text page.

Every result is cached to artifacts/<doc_id>/enriched.json, so re-running
an ingest costs nothing and never re-spends free-tier quota.
"""

import json
import time

from app import config
from app.llm import ask_json
from app.models import ImageDescription, PageTranscript

TRANSCRIBE_PROMPT = """You are transcribing one page of a PDF that has no text layer,
so everything on it is an image. Your transcription is the ONLY way this page's
content becomes searchable, so it must be complete.

Return JSON with three fields:

1. "markdown" - a faithful, complete transcription of every word on the page.
   - Preserve the structure: headings as markdown headings, lists as lists.
   - Render tables as markdown tables with all rows and columns.
   - Render code as fenced code blocks, keeping indentation.
   - Transcribe, do not summarise. Do not add commentary or invent content.
   - If some text is unclear, give your best reading rather than skipping it.
   - IMPORTANT: use real line breaks. Every heading, paragraph, list item,
     table row and line of code must be on its own line. Never run the whole
     page together as a single line of text.

2. "page_summary" - one sentence describing what this page covers.

3. "figures" - one entry for each MEANINGFUL visual element: a diagram, chart,
   flow, screenshot, code screenshot, or photo. For each one give a short
   "caption", a detailed "description" (actors, arrows, labels, values, and any
   text inside it), and a "kind".
   Ignore purely decorative items: logos, icons, bullets, rules, background art.
   If the page has no meaningful figure, return an empty list."""

DESCRIBE_PROMPT = """Describe this figure taken from a document so that someone
searching in words can find it later.

Return JSON with:
  "caption"        - a short title
  "description"    - detail: what it shows, actors, arrows, labels, axis values,
                     table contents, code content
  "extracted_text" - every piece of text visible inside the image, verbatim
  "kind"           - diagram, chart, screenshot, table, or photo

Text surrounding this figure on the page (use it to resolve references like
"Figure 4", but describe only the image itself):
---
{context}
---"""


class Cache:
    """A tiny JSON file so a re-run never repeats a paid/limited call."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def __len__(self):
        return len(self.data)


def _throttle():
    """Stay under the free-tier requests-per-minute limit."""
    time.sleep(config.GEMINI_SLEEP_SECONDS)


def transcribe_page(png_bytes, page_number, cache):
    """Full-page vision transcription. Returns a dict shaped like PageTranscript."""
    key = f"page:{page_number}"
    hit = cache.get(key)
    if hit:
        return hit

    result, _model = ask_json(
        TRANSCRIBE_PROMPT,
        schema=PageTranscript,
        images=[(png_bytes, "image/png")],
        needs_vision=True,
    )
    result.setdefault("markdown", "")
    result.setdefault("page_summary", "")
    result.setdefault("figures", [])

    cache.set(key, result)
    _throttle()
    return result


def describe_image(png_bytes, context_text, key, cache):
    """Describe one extracted figure. Returns a dict shaped like ImageDescription."""
    hit = cache.get(key)
    if hit:
        return hit

    result, _model = ask_json(
        DESCRIBE_PROMPT.format(context=(context_text or "")[:1500]),
        schema=ImageDescription,
        images=[(png_bytes, "image/png")],
        needs_vision=True,
    )
    result.setdefault("caption", "")
    result.setdefault("description", "")
    result.setdefault("extracted_text", "")
    result.setdefault("kind", "diagram")

    cache.set(key, result)
    _throttle()
    return result
