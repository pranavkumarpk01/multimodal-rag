"""
Look at a PDF before ingesting it.

Answers three questions:
  1. Is the text extractable, or is this PDF really just pictures of text?
  2. How many embedded images are there, and how many survive the filters?
  3. Are there tables PyMuPDF can detect?

Usage:  py scripts/probe_pdf.py data/YourFile.pdf
"""

import io
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # PyMuPDF
from PIL import Image

from app import config


def flat_colour_ratio(png_bytes):
    """Fraction of pixels that are the single most common colour."""
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    img.thumbnail((64, 64))
    total = img.width * img.height
    colours = img.getcolors(maxcolors=total)  # [(count, rgb), ...]
    return max(count for count, _ in colours) / total


def probe(pdf_path):
    doc = fitz.open(pdf_path)
    print(f"\n{Path(pdf_path).name}  -  {doc.page_count} pages")
    print("=" * 78)
    print(f"{'pg':>3} {'chars':>7} {'imgs':>5} {'kept':>5} {'tbls':>5}  notes")
    print("-" * 78)

    total_chars = kept_total = raw_total = table_total = 0
    kept_details = []

    for number, page in enumerate(doc, start=1):
        text = page.get_text().strip()
        raw_images = page.get_images(full=True)

        kept, notes = 0, []
        for index, info in enumerate(raw_images):
            xref = info[0]
            try:
                pix = fitz.Pixmap(doc, xref)
                if pix.n - pix.alpha >= 4:            # CMYK -> RGB
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                data = pix.tobytes("png")
                pixels = pix.width * pix.height
            except Exception as err:
                notes.append(f"img{index} unreadable ({type(err).__name__})")
                continue

            if pixels < config.MIN_IMAGE_PIXELS:
                continue
            if flat_colour_ratio(data) > config.MAX_FLAT_COLOUR_RATIO:
                continue

            kept += 1
            kept_details.append((number, index, pix.width, pix.height, len(data)))

        try:
            tables = len(page.find_tables().tables)
        except Exception:
            tables = 0

        if len(text) < 200:
            notes.append("LOW TEXT - likely a picture of text")

        print(f"{number:>3} {len(text):>7} {len(raw_images):>5} {kept:>5} "
              f"{tables:>5}  {'; '.join(notes)}")

        total_chars += len(text)
        raw_total += len(raw_images)
        kept_total += kept
        table_total += tables

    print("-" * 78)
    print(f"{'':>3} {total_chars:>7} {raw_total:>5} {kept_total:>5} {table_total:>5}  TOTAL")

    print("\nImages that survived the filters:")
    if kept_details:
        for pg, idx, w, h, size in kept_details:
            print(f"   page {pg:>2}  img{idx}  {w}x{h}px  {size / 1024:.0f} KB")
    else:
        print("   none")

    print("\nVerdict")
    avg = total_chars / max(doc.page_count, 1)
    if avg < 300:
        print("   Text layer is thin. This PDF is mostly graphics - page renders")
        print("   plus vision will do the heavy lifting, not text extraction.")
    else:
        print(f"   Healthy text layer ({avg:.0f} chars/page average).")
    print(f"   Vision calls needed for enrichment: ~{kept_total}")
    print(f"   At {config.GEMINI_SLEEP_SECONDS}s throttle that is about "
          f"{kept_total * config.GEMINI_SLEEP_SECONDS / 60:.1f} minutes.\n")

    doc.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        pdfs = sorted(config.DATA_DIR.glob("*.pdf"))
        if not pdfs:
            sys.exit("No PDF given and none found in data/")
        target = str(pdfs[0])
    probe(target)
