"""
The only file that talks to an LLM provider.

Two public functions:

    ask(prompt, images=None) -> (answer_text, which_model)
    embed(texts)             -> list of vectors

`ask` walks config.LLM_CHAIN top to bottom until a model answers.
A text-only model simply gets no images - which is safe, because callers
must always put the image DESCRIPTIONS in the prompt text. Images are an
enhancement, never a requirement.

Run a self-test with:   py -m app.llm
"""

import base64
import json
import math
import re
import time

from google import genai
from google.genai import types
from groq import Groq

from app import config

_gemini = genai.Client(api_key=config.GOOGLE_API_KEY)
_groq = Groq(api_key=config.GROQ_API_KEY)


# ----------------------------------------------------------------------
# provider callers - each takes (model, prompt, images) and returns text
# `images` is a list of (bytes, mime_type) tuples, or None
# ----------------------------------------------------------------------
def _call_gemini(model, prompt, images):
    parts = [prompt]
    for data, mime in images or []:
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    reply = _gemini.models.generate_content(model=model, contents=parts)
    return (reply.text or "").strip()


def _call_groq(model, prompt, images):
    if images:
        content = [{"type": "text", "text": prompt}]
        for data, mime in images:
            b64 = base64.b64encode(data).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
        message = {"role": "user", "content": content}
    else:
        message = {"role": "user", "content": prompt}

    reply = _groq.chat.completions.create(model=model, messages=[message])
    return (reply.choices[0].message.content or "").strip()


CALLERS = {"gemini": _call_gemini, "groq": _call_groq}


# ----------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------
def ask(prompt, images=None):
    """Ask the chain. Returns (answer, "provider:model"). Raises if all fail."""
    failures = []
    for provider, model, can_see in config.LLM_CHAIN:
        try:
            text = CALLERS[provider](model, prompt, images if can_see else None)
            if not text:
                raise RuntimeError("empty response")
            return text, f"{provider}:{model}"
        except Exception as err:
            failures.append(f"  {provider}:{model} -> {type(err).__name__}: {err}")
            print(f"[llm] {provider}:{model} failed, trying next in chain")
    raise RuntimeError("Every provider in LLM_CHAIN failed:\n" + "\n".join(failures))


def ask_json(prompt, schema, images=None, needs_vision=False):
    """
    Like ask(), but the model must return JSON matching `schema`
    (a pydantic model class). Returns the parsed dict.

    needs_vision=True skips text-only models entirely - used by enrichment,
    where a model that cannot see the image is useless rather than degraded.

    Returns (parsed_dict, "provider:model").
    """
    failures = []
    for provider, model, can_see in config.LLM_CHAIN:
        if needs_vision and not can_see:
            continue
        try:
            if provider == "gemini":
                parts = [prompt]
                for data, mime in images or []:
                    parts.append(types.Part.from_bytes(data=data, mime_type=mime))
                reply = _gemini.models.generate_content(
                    model=model,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                    ),
                )
                raw = reply.text
            else:
                reply = _groq.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt + "\n\nRespond with JSON only."}],
                    response_format={"type": "json_object"},
                )
                raw = reply.choices[0].message.content

            return json.loads(raw), f"{provider}:{model}"

        except Exception as err:
            failures.append(f"  {provider}:{model} -> {type(err).__name__}: {err}")
            reason = str(err).split("\n")[0][:110]
            print(f"[llm] {provider}:{model} json call failed "
                  f"({type(err).__name__}: {reason}), trying next in chain")

    raise RuntimeError("No provider produced valid JSON:\n" + "\n".join(failures))


def _normalise(vector):
    """Scale to unit length - recommended when shortening the embedding."""
    length = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / length for v in vector]


_RETRY_HINT = re.compile(r"retry in ([\d.]+)\s*s", re.I)


def _retry_after(err, fallback):
    """
    Honour the server's own retry advice.

    A 429 from Gemini carries 'Please retry in 34.5s'. Exponential backoff
    that tops out below that number just burns attempts and then fails - so
    when the server tells us how long to wait, we wait that long.
    """
    match = _RETRY_HINT.search(str(err))
    if match:
        return min(float(match.group(1)) + 2.0, 120.0)
    return fallback


def embed(texts, task_type=None, attempts=6):
    """
    Embed a list of strings. Retries, but NEVER switches model - vectors from
    two different models are not comparable, so a fallback here would corrupt
    the index silently rather than fail loudly.
    """
    if isinstance(texts, str):
        texts = [texts]

    settings = types.EmbedContentConfig(
        task_type=task_type or config.EMBED_TASK_DOCUMENT,
        output_dimensionality=config.EMBED_DIM,
    )

    last = None
    for n in range(attempts):
        try:
            result = _gemini.models.embed_content(
                model=config.EMBED_MODEL, contents=texts, config=settings
            )
            return [_normalise(e.values) for e in result.embeddings]
        except Exception as err:
            last = err
            wait = _retry_after(err, 2 ** n)
            reason = str(err).split("\n")[0][:90]
            print(f"[embed] attempt {n + 1}/{attempts} failed ({reason}); "
                  f"waiting {wait:.0f}s")
            time.sleep(wait)
    raise RuntimeError(f"Embedding failed after {attempts} attempts: {last}")


def embed_query(text):
    """Embed a search query. Uses a different task type than documents."""
    return embed([text], task_type=config.EMBED_TASK_QUERY)[0]


# ----------------------------------------------------------------------
# self-test:  py -m app.llm
# ----------------------------------------------------------------------
def _tiny_png():
    """A 32x32 red square, used to check whether a model really sees images."""
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (32, 32), (220, 30, 30)).save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def _check(label, fn):
    try:
        detail = fn()
        print(f"  OK    {label:52s} {detail}")
        return True
    except Exception as err:
        msg = str(err).replace("\n", " ")[:110]
        print(f"  FAIL  {label:52s} {type(err).__name__}: {msg}")
        return False


def self_test():
    print(f"\n{config.PROJECT_NAME} - connection check")
    print("=" * 78)

    ok = True

    print("\nKeys")
    ok &= _check("GOOGLE_API_KEY present", lambda: (
        f"len {len(config.GOOGLE_API_KEY)}" if config.GOOGLE_API_KEY
        else (_ for _ in ()).throw(RuntimeError("empty"))))
    ok &= _check("GROQ_API_KEY present", lambda: (
        f"len {len(config.GROQ_API_KEY)}" if config.GROQ_API_KEY
        else (_ for _ in ()).throw(RuntimeError("empty"))))

    print("\nQdrant")

    def qdrant_check():
        from qdrant_client import QdrantClient
        client = QdrantClient(url=config.QDRANT_URL)
        names = [c.name for c in client.get_collections().collections]
        return f"{config.QDRANT_URL}  collections={names or 'none yet'}"

    ok &= _check("connection", qdrant_check)

    print("\nEmbeddings (no fallback by design)")
    ok &= _check(config.EMBED_MODEL, lambda: (
        f"{len(embed(['hello world'])[0])} dims"))

    print("\nAnswer chain")
    image = _tiny_png()
    for provider, model, can_see in config.LLM_CHAIN:
        caller = CALLERS[provider]
        ok &= _check(f"{provider}:{model} (text)", lambda c=caller, m=model: (
            c(m, "Reply with exactly: PONG", None)[:40]))
        if can_see:
            _check(f"{provider}:{model} (vision)", lambda c=caller, m=model: (
                c(m, "What colour is this image? One word.", [image])[:40]))

    print("\n" + "=" * 78)
    if ok:
        print("All required checks passed - ready to ingest.\n")
    else:
        print("Some checks FAILED. Available Groq models for reference:")
        try:
            for m in sorted(x.id for x in _groq.models.list().data):
                print("   ", m)
        except Exception as err:
            print("    could not list Groq models:", err)
        print()
    return ok


if __name__ == "__main__":
    self_test()
