"""
All settings in one place. Reads .env once, at import.

Nothing here does any work - it just holds values other modules read.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the folder above app/
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _get(name, default=""):
    return os.getenv(name, default).strip()


# --- identity -----------------------------------------------------------
PROJECT_NAME = _get("PROJECT_NAME", "Multimodal RAG")
API_V1_STR = _get("API_V1_STR", "/api/v1")

# --- API keys -----------------------------------------------------------
GOOGLE_API_KEY = _get("GOOGLE_API_KEY")
GROQ_API_KEY = _get("GROQ_API_KEY")

# --- Qdrant -------------------------------------------------------------
QDRANT_URL = _get("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = _get("QDRANT_COLLECTION", "multimodal_rag")

# --- local folders ------------------------------------------------------
DATA_DIR = ROOT / _get("DATA_DIR", "data")
ARTIFACT_DIR = ROOT / _get("ARTIFACT_DIR", "artifacts")
DATA_DIR.mkdir(exist_ok=True)
ARTIFACT_DIR.mkdir(exist_ok=True)

# --- models -------------------------------------------------------------
# The answer chain. Tried top to bottom until one succeeds.
# can_see_images=False means: still usable, just gets text only.
# Groq currently has NO vision model, so both Groq entries are text-only.
# The two Gemini entries have separate free-tier quotas, which is why the
# second one is a useful fallback rather than a duplicate.
LLM_CHAIN = [
    # (provider, model, can_see_images)
    ("gemini", "gemini-3.5-flash", True),
    ("gemini", "gemini-flash-lite-latest", True),
    ("groq", "llama-3.3-70b-versatile", False),
    ("groq", "llama-3.1-8b-instant", False),
]

# Embeddings have NO fallback on purpose: vectors from different models are
# not comparable, so switching model silently breaks search. Retry only.
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768  # model default is 3072; 768 is smaller/faster and plenty here

# Task types tell the embedder whether it is encoding a document or a query.
# Using the right one measurably improves retrieval and costs nothing.
EMBED_TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
EMBED_TASK_QUERY = "RETRIEVAL_QUERY"

# --- free-tier throttle -------------------------------------------------
# Seconds to wait between Gemini vision calls while ingesting.
GEMINI_SLEEP_SECONDS = float(_get("GEMINI_SLEEP_SECONDS", "4.5") or 4.5)

# Embedding is capped at ~100 items per MINUTE on the free tier, and every
# text inside a batch counts separately. So the safe rate is:
#     EMBED_BATCH_SIZE / EMBED_SLEEP_SECONDS  <  100/60 items per second
# 16 items every 11s = ~87/min, which leaves headroom for a retry.
EMBED_BATCH_SIZE = int(_get("EMBED_BATCH_SIZE", "16") or 16)
EMBED_SLEEP_SECONDS = float(_get("EMBED_SLEEP_SECONDS", "11") or 11)

# --- ingestion tuning ---------------------------------------------------
MIN_IMAGE_PIXELS = 10_000     # smaller than this = decoration, skip it
MAX_FLAT_COLOUR_RATIO = 0.95  # 95%+ one colour = rule/fill, skip it
PAGE_RENDER_DPI = 200
CHUNK_WORDS = 700
CHUNK_OVERLAP_WORDS = 80

# --- retrieval tuning ---------------------------------------------------
TOP_TEXT = 8    # text chunks sent to the model
TOP_IMAGES = 3  # images sent to the model (the modality quota)
CANDIDATES = 40  # how many to pull before fusing

# --- answering ----------------------------------------------------------
# Page renders are ~1700x2200 at 200 DPI. Sending that costs a lot of tokens
# for little gain, so images are downscaled before being attached. Big enough
# that labels inside a diagram stay readable.
ANSWER_IMAGE_MAX_PX = 1280
MAX_CHARS_PER_TEXT_CHUNK = 1800   # trim very long chunks in the prompt
