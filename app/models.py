"""
The data shapes the whole pipeline passes around.

There is deliberately only ONE record type. A paragraph, a diagram and a
table are all a Chunk - they differ only by `kind` and whether they carry
an `image_path`. That keeps storing, searching and ranking simple: one
collection, one code path.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------
# Internal record (what we store in Qdrant)
# ----------------------------------------------------------------------
@dataclass
class Chunk:
    id: str                      # "doc_id:p12:t3"
    doc_id: str
    page: int
    kind: str                    # "text" | "image"
    text: str                    # the searchable surface
    heading: str = ""            # section trail, e.g. "3. Core Blocks > 3.1 Model layer"
    image_path: Optional[str] = None   # set only when kind == "image"

    def to_dict(self):
        return asdict(self)


@dataclass
class IngestResult:
    doc_id: str
    source: str
    pages: int
    chunks: List[Chunk] = field(default_factory=list)
    images: int = 0
    vision_pages: int = 0        # pages that needed transcription
    text_pages: int = 0          # pages with a usable text layer


# ----------------------------------------------------------------------
# Schemas the vision model must fill in (used as JSON schemas by Gemini)
# ----------------------------------------------------------------------
class Figure(BaseModel):
    caption: str = Field(description="Short title for the figure")
    description: str = Field(
        description="Detailed description: what it shows, actors, arrows, "
                    "labels, axis values, code content, table contents"
    )
    kind: str = Field(description="One of: diagram, chart, screenshot, table, photo")


class PageTranscript(BaseModel):
    """Returned for a page that has no text layer."""
    markdown: str = Field(
        description="Faithful, complete transcription of every word on the page"
    )
    page_summary: str = Field(description="One sentence on what this page covers")
    figures: List[Figure] = Field(
        description="One entry per meaningful figure. Empty list if the page is only text."
    )


class AnswerPayload(BaseModel):
    """What the model must return when answering a question."""
    answer: str = Field(description="The answer, in markdown, citing page numbers")
    used_image_ids: List[str] = Field(
        description="Labels of the figures you actually relied on, e.g. ['IMAGE-1']. "
                    "Empty list if none were needed."
    )
    used_pages: List[int] = Field(description="Page numbers the answer draws on")


class ImageDescription(BaseModel):
    """Returned for a single image extracted from a text-based page."""
    caption: str
    description: str
    extracted_text: str = Field(description="All text visible inside the image")
    kind: str = Field(description="One of: diagram, chart, screenshot, table, photo")
