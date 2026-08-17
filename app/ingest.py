"""
PDF in, Chunks out.

Every page takes one of two routes, decided automatically:

  TEXT PAGE   (has a real text layer)
      use the extracted text, and enrich each embedded figure separately

  VISION PAGE (no text layer - a scan or an exported design)
      send the whole page image to the vision model, which returns both a
      full transcription AND a list of the figures on it

Both routes end in the same place: markdown text that gets chunked, plus
image records that point at a PNG on disk. One code path from there on.
"""

import hashlib
import json
import re
import statistics
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
import io

from app import config
from app.enrich import Cache, describe_image, transcribe_page
from app.models import Chunk, IngestResult

# A page with less text than this is treated as a picture of a page.
TEXT_PAGE_MIN_CHARS = 200

# A section shorter than this is noise (a stray word, a page number).
# It used to be 15, which silently threw away real content: a "Tools:"
# heading followed by six product names is only ~10 words.
MIN_SECTION_WORDS = 4


# ----------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------
def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def flat_colour_ratio(png_bytes):
    """Fraction of pixels that are the single most common colour."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img.thumbnail((64, 64))
    total = img.width * img.height
    colours = img.getcolors(maxcolors=total)
    return max(count for count, _ in colours) / total


def page_to_markdown(page):
    """
    Extract text and mark headings with '##', using font size.
    Turning text pages into markdown means ONE chunker handles both routes.
    """
    data = page.get_text("dict")
    text_blocks = [b for b in data["blocks"] if b.get("type") == 0]

    sizes = [
        span["size"]
        for block in text_blocks
        for line in block["lines"]
        for span in line["spans"]
    ]
    if not sizes:
        return ""
    body_size = statistics.median(sizes)

    lines_out = []
    for block in text_blocks:
        for line in block["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text:
                continue
            biggest = max(span["size"] for span in line["spans"])
            is_heading = biggest >= body_size * 1.15 and len(text) < 80
            lines_out.append(f"## {text}" if is_heading else text)
        lines_out.append("")
    return "\n".join(lines_out)


def tidy_markdown(markdown):
    """
    Repair transcriptions that came back without line breaks.

    Vision models sometimes return the whole page as one long line, which
    hides every heading from the chunker. We put a break back in front of
    anything that looks like a heading or a code fence.

    The '[A-Z0-9]' guard stops us splitting on '# fast, cheap' style comments
    inside code, which start with a lowercase letter.
    """
    if not markdown:
        return ""
    markdown = re.sub(r"(?<!\n)(#{1,6} [A-Z0-9])", r"\n\n\1", markdown)
    markdown = re.sub(r"(?<!\n)(```)", r"\n\1", markdown)
    return markdown


def chunk_markdown(markdown, max_words=None, overlap=None):
    """
    Split on headings first, then window long sections.
    Returns a list of (heading, text) pairs.
    """
    markdown = tidy_markdown(markdown)
    max_words = max_words or config.CHUNK_WORDS
    overlap = overlap or config.CHUNK_OVERLAP_WORDS

    sections, heading, buffer = [], "", []
    for line in markdown.splitlines():
        if line.lstrip().startswith("#"):
            if buffer:
                sections.append((heading, "\n".join(buffer).strip()))
                buffer = []
            heading = line.lstrip("#").strip()
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, "\n".join(buffer).strip()))

    chunks = []
    step = max(max_words - overlap, 1)
    for head, body in sections:
        words = body.split()
        if len(words) < MIN_SECTION_WORDS:
            continue
        for start in range(0, len(words), step):
            piece = " ".join(words[start:start + max_words])
            chunks.append((head, piece))
            if start + max_words >= len(words):
                break
    return chunks


def image_fingerprint(png_bytes):
    """Content hash. Two identical PNGs anywhere in the PDF share one."""
    return hashlib.md5(png_bytes).hexdigest()


def figure_to_text(figure):
    """Flatten a figure description into the string we will search on."""
    parts = [figure.get("caption", ""), figure.get("description", "")]
    inside = figure.get("extracted_text", "")
    if inside:
        parts.append(f"Text shown in the figure: {inside}")
    return "\n".join(p for p in parts if p).strip()


# ----------------------------------------------------------------------
# main entry point
# ----------------------------------------------------------------------
def ingest_pdf(pdf_path, verbose=True):
    pdf_path = Path(pdf_path)
    doc_id = slugify(pdf_path.stem)
    out_dir = config.ARTIFACT_DIR / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)

    cache = Cache(out_dir / "enriched.json")
    # Content hash -> page it first appeared on. Headers, footers and logos
    # repeat on every page; describing each copy wastes vision quota and
    # fills the index with near-identical vectors.
    seen_images = {}
    doc = fitz.open(pdf_path)
    result = IngestResult(doc_id=doc_id, source=str(pdf_path), pages=doc.page_count)

    if verbose:
        print(f"\ningesting {pdf_path.name}  ({doc.page_count} pages)")
        print(f"artifacts -> {out_dir}")
        print(f"cache has {len(cache)} entries from previous runs\n")

    for number, page in enumerate(doc, start=1):
        raw_text = page.get_text().strip()

        # Always render the page - it is the safety net and, for vision
        # pages, the actual artifact we return to the user.
        render = page.get_pixmap(dpi=config.PAGE_RENDER_DPI)
        page_png = out_dir / f"page_{number:03d}.png"
        render.save(page_png)

        if len(raw_text) >= TEXT_PAGE_MIN_CHARS:
            _ingest_text_page(doc, page, number, doc_id, out_dir,
                              cache, seen_images, result, verbose)
        else:
            _ingest_vision_page(page_png, number, doc_id, cache, result, verbose)

    doc.close()
    _write_manifest(out_dir, result)

    if verbose:
        print(f"\ndone: {len(result.chunks)} chunks "
              f"({result.text_pages} text pages, {result.vision_pages} vision pages, "
              f"{result.images} images)")
        print(f"manifest -> {out_dir / 'manifest.json'}\n")
    return result


def _ingest_text_page(doc, page, number, doc_id, out_dir,
                      cache, seen_images, result, verbose):
    """Page has a usable text layer: use it, and enrich figures one by one."""
    result.text_pages += 1
    markdown = page_to_markdown(page)

    for index, (heading, text) in enumerate(chunk_markdown(markdown)):
        result.chunks.append(Chunk(
            id=f"{doc_id}:p{number}:t{index}",
            doc_id=doc_id, page=number, kind="text",
            text=text, heading=heading,
        ))

    kept = repeated = 0
    for index, info in enumerate(page.get_images(full=True)):
        xref = info[0]
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha >= 4:          # CMYK -> RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            png = pix.tobytes("png")
        except Exception:
            continue

        if pix.width * pix.height < config.MIN_IMAGE_PIXELS:
            continue
        if flat_colour_ratio(png) > config.MAX_FLAT_COLOUR_RATIO:
            continue

        fingerprint = image_fingerprint(png)
        if fingerprint in seen_images:
            repeated += 1
            continue
        seen_images[fingerprint] = number

        image_path = out_dir / f"page_{number:03d}_img_{index:02d}.png"
        image_path.write_bytes(png)

        described = describe_image(
            png, page.get_text(), key=f"img:{number}:{index}", cache=cache
        )
        result.chunks.append(Chunk(
            id=f"{doc_id}:p{number}:i{index}",
            doc_id=doc_id, page=number, kind="image",
            text=figure_to_text(described),
            heading=described.get("caption", ""),
            image_path=str(image_path),
        ))
        kept += 1
        result.images += 1

    if verbose:
        note = f"  ({repeated} repeated, skipped)" if repeated else ""
        print(f"  page {number:>3}  text   {len(markdown):>6} chars  {kept} images{note}")


def _ingest_vision_page(page_png, number, doc_id, cache, result, verbose):
    """No text layer: the vision model transcribes the page and lists figures."""
    result.vision_pages += 1
    png_bytes = page_png.read_bytes()
    transcript = transcribe_page(png_bytes, number, cache)

    markdown = transcript.get("markdown", "")
    summary = transcript.get("page_summary", "")
    figures = transcript.get("figures", [])

    # 1. the transcribed words become ordinary text chunks
    for index, (heading, text) in enumerate(chunk_markdown(markdown)):
        result.chunks.append(Chunk(
            id=f"{doc_id}:p{number}:t{index}",
            doc_id=doc_id, page=number, kind="text",
            text=text, heading=heading,
        ))

    # 2. the page image itself is the retrievable picture. Its searchable
    #    text is the summary plus every figure description on the page.
    figure_text = "\n\n".join(figure_to_text(f) for f in figures)
    image_text = "\n\n".join(p for p in [summary, figure_text] if p)
    if not image_text:
        image_text = " ".join(markdown.split()[:120])

    result.chunks.append(Chunk(
        id=f"{doc_id}:p{number}:i0",
        doc_id=doc_id, page=number, kind="image",
        text=image_text,
        heading=figures[0].get("caption", "") if figures else summary,
        image_path=str(page_png),
    ))
    result.images += 1

    if verbose:
        print(f"  page {number:>3}  vision {len(markdown):>6} chars  "
              f"{len(figures)} figures  \"{summary[:52]}\"")


def _write_manifest(out_dir, result):
    manifest = {
        "doc_id": result.doc_id,
        "source": result.source,
        "pages": result.pages,
        "text_pages": result.text_pages,
        "vision_pages": result.vision_pages,
        "images": result.images,
        "chunk_count": len(result.chunks),
        "chunks": [c.to_dict() for c in result.chunks],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
