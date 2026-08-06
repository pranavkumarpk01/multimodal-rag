"""
Drawing diagrams instead of only finding them.

This is the "create an image" branch, done the cheap and honest way:
the model writes Graphviz DOT source and the browser renders it.

Why source code rather than a text-to-image model:

  free         it is just text generation - the same chain already in use
  faithful     labels are exact strings, not pixels a diffusion model painted
  editable     you can fix one node without regenerating everything
  no install   Streamlit renders DOT natively via st.graphviz_chart()

The tradeoff is scope: this draws structure - flows, hierarchies, sequences,
state machines. It cannot produce an illustration or a photo.

Anything produced here is marked source="generated" so it can never be
mistaken for a figure actually extracted from the document.
"""

import re

from pydantic import BaseModel, Field

from app import config
from app.llm import ask_json

# Cheap pre-filter. Most questions are plainly lookups, and those should not
# pay for a classification call at all. Only maybes go to the model.
DRAW_HINTS = re.compile(
    r"\b(draw|sketch|diagram|flow ?chart|flowchart|visuali[sz]e|visuali[sz]ation|"
    r"chart|graph|mind ?map|create an image|generate an image|make an image|"
    r"illustrate|plot)\b",
    re.I,
)

CLASSIFY_PROMPT = """Decide what the user wants.

"find"  - they want information, or an existing figure from the document
          ("show me the auth diagram", "what does figure 3 say")
"draw"  - they want a NEW diagram created for them
          ("draw the flow", "make a chart of the stages")

Question: {question}

Return JSON: {{"intent": "find" or "draw"}}"""

DIAGRAM_PROMPT = """Draw a diagram using ONLY the document excerpts below.

Return JSON with:
  "title"       - a short title
  "dot"         - valid Graphviz DOT source
  "explanation" - two sentences on what the diagram shows

Rules for the DOT source:
- Start with `digraph G {{` and close it. Nothing before or after.
- Set `rankdir=TB` for hierarchies and stages, `rankdir=LR` for pipelines.
- Add: node [shape=box, style="rounded,filled", fillcolor="#EEF2FF",
  color="#4F46E5", fontname="Segoe UI", fontsize=10];
- Every label must be wrapped in double quotes. Use \\n for line breaks
  inside a label. Never use single quotes.
- Use short labels taken from the source text. Do not invent steps that are
  not in the excerpts.
- Use subgraph cluster_x with a label to group related stages.
- Keep it under 25 nodes so it stays readable.

=== DOCUMENT EXCERPTS ===
{context}
=== END EXCERPTS ===

Request: {question}"""


class Intent(BaseModel):
    intent: str = Field(description="Either 'find' or 'draw'")


class DiagramSpec(BaseModel):
    title: str
    dot: str = Field(description="Valid Graphviz DOT source, starting with 'digraph'")
    explanation: str


def wants_diagram(question):
    """
    True if the user is asking for a NEW diagram.

    Keyword pre-filter first so ordinary lookups cost nothing. Only questions
    that look like a drawing request get a (free, fast) classification call.
    """
    if not DRAW_HINTS.search(question):
        return False
    try:
        data, _model = ask_json(CLASSIFY_PROMPT.format(question=question), schema=Intent)
        return data.get("intent", "find").lower().strip() == "draw"
    except Exception as err:
        print(f"[diagram] intent check failed ({err}); treating as a normal question")
        return False


def looks_like_dot(source):
    """Cheap structural check - we have no graphviz binary to validate with."""
    if not source or "digraph" not in source:
        return False
    return source.count("{") == source.count("}") and source.count("{") >= 1


def clean_dot(source):
    """Strip markdown fences the model sometimes adds despite being told not to."""
    source = (source or "").strip()
    source = re.sub(r"^```(?:dot|graphviz)?\s*", "", source)
    source = re.sub(r"\s*```$", "", source)
    return source.strip()


def generate(question, text_hits, max_chunks=6):
    """
    Build a diagram from retrieved text. Returns a dict, or None if the model
    could not produce usable DOT after a retry.
    """
    excerpts = []
    for hit in text_hits[:max_chunks]:
        payload = hit["payload"]
        heading = payload.get("heading") or "(no heading)"
        body = payload["text"][:config.MAX_CHARS_PER_TEXT_CHUNK]
        excerpts.append(f"[page {payload['page']}] {heading}\n{body}")
    context = "\n\n".join(excerpts) or "(no relevant text found)"

    prompt = DIAGRAM_PROMPT.format(context=context, question=question)

    for attempt in (1, 2):
        try:
            data, model_used = ask_json(prompt, schema=DiagramSpec)
        except Exception as err:
            print(f"[diagram] generation failed: {err}")
            return None

        dot = clean_dot(data.get("dot", ""))
        if looks_like_dot(dot):
            return {
                "title": data.get("title", "Generated diagram"),
                "format": "dot",
                "source": dot,
                "explanation": data.get("explanation", ""),
                "model": model_used,
                "origin": "generated",   # never confuse this with a real page
                "pages_used": sorted({h["payload"]["page"] for h in text_hits[:max_chunks]}),
            }
        print(f"[diagram] attempt {attempt}: model returned unusable DOT")

    return None
