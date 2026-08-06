# -*- coding: utf-8 -*-
"""
Convert a Markdown file into the block list build_docs.py renders.

This exists so a document has ONE source. The .md is the thing you edit and
read on GitHub; the PDF is generated from it. They cannot drift apart.

Supported:
    # / ## / ###        headings
    paragraphs          with **bold** and `inline code`
    - item / * item     bullets
    1. item             numbered list
    | a | b |           tables (with the |---| separator row)
    ```lang ... ```     code block
    ```diagram Title    ASCII diagram with a caption
    > [!key] Title      callout; kinds: key, warn, info, tip
    ---                 horizontal rule
    <!-- pagebreak -->  force a new page
    <!-- meta: k = v -->  cover metadata (before the first heading)
"""

import re

CALLOUT_RE = re.compile(r"^>\s*\[!(\w+)\]\s*(.*)$")
META_RE = re.compile(r"^<!--\s*meta:\s*([^=]+?)\s*=\s*(.*?)\s*-->$")
TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


def _split_row(line):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def blocks_from_markdown(path):
    """Returns (blocks, doc_title, cover_meta)."""
    lines = path.read_text(encoding="utf-8").splitlines()

    blocks = []
    meta = []
    title = path.stem.replace("_", " ")
    i = 0

    def flush(buffer):
        if buffer:
            blocks.append(("p", " ".join(buffer).strip()))
        return []

    paragraph = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # --- comments: metadata, page breaks -------------------------
        if stripped.startswith("<!--"):
            m = META_RE.match(stripped)
            if m:
                meta.append((m.group(1), m.group(2)))
            elif "pagebreak" in stripped:
                paragraph = flush(paragraph)
                blocks.append(("pagebreak", None))
            i += 1
            continue

        # --- fenced blocks -------------------------------------------
        if stripped.startswith("```"):
            info = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            paragraph = flush(paragraph)
            text = "\n".join(body)
            if info.lower().startswith("diagram"):
                caption = info[7:].strip() or "Diagram"
                blocks.append(("diagram", (caption, text)))
            else:
                blocks.append(("code", text))
            continue

        # --- callouts -------------------------------------------------
        m = CALLOUT_RE.match(stripped)
        if m:
            kind = m.group(1).lower()
            kind = kind if kind in ("key", "warn", "info", "tip") else "info"
            ctitle = m.group(2).strip()
            body = []
            i += 1
            while i < len(lines) and lines[i].strip().startswith(">"):
                body.append(lines[i].strip().lstrip(">").strip())
                i += 1
            paragraph = flush(paragraph)
            blocks.append(("callout", (kind, ctitle, " ".join(body).strip())))
            continue

        # --- tables ---------------------------------------------------
        if stripped.startswith("|") and i + 1 < len(lines) and \
                TABLE_SEP_RE.match(lines[i + 1].strip()):
            headers = _split_row(stripped)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            paragraph = flush(paragraph)
            width = len(headers)
            rows = [(r + [""] * width)[:width] for r in rows]
            blocks.append(("table", (headers, rows)))
            continue

        # --- lists ----------------------------------------------------
        if re.match(r"^[-*]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            paragraph = flush(paragraph)
            blocks.append(("bullets", items))
            continue

        if re.match(r"^\d+[.)]\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+[.)]\s+", "", lines[i].strip()))
                i += 1
            paragraph = flush(paragraph)
            blocks.append(("numbers", items))
            continue

        # --- headings -------------------------------------------------
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            paragraph = flush(paragraph)
            if level == 1 and not blocks:
                title = text          # first H1 is the document title
            else:
                blocks.append((f"h{min(level, 3)}", text))
            i += 1
            continue

        # --- rule -----------------------------------------------------
        if stripped in ("---", "***", "___"):
            paragraph = flush(paragraph)
            blocks.append(("rule", None))
            i += 1
            continue

        # --- blank line ends a paragraph -------------------------------
        if not stripped:
            paragraph = flush(paragraph)
            i += 1
            continue

        paragraph.append(stripped)
        i += 1

    flush(paragraph)
    return blocks, title, meta


def build(path, subtitle=""):
    """Full block list including a cover page."""
    blocks, title, meta = blocks_from_markdown(path)
    cover = ("cover", {
        "title": title,
        "subtitle": subtitle or "Code Walkthrough",
        "meta": meta or [("Source", path.name)],
    })
    return [cover, ("pagebreak", None)] + blocks, title
