# Setup Guide — Multimodal RAG

Follow these steps in order. Total first-time setup: ~10 minutes, plus
ingestion time (a few minutes per PDF, throttled for free-tier API limits).

## 0. Prerequisites

- Python 3.10+
- Docker Desktop (for Qdrant)
- A Google AI Studio API key: https://aistudio.google.com/apikey
- A Groq API key (free): https://console.groq.com/keys

## 1. Start Qdrant (the vector database)

From the project root (`multimodal-rag/`):

```bash
docker compose up -d
```

This starts Qdrant on `localhost:6333` (REST + dashboard) and `localhost:6334`
(gRPC), storing data in `./qdrant_storage`. Check it's up:

```
http://localhost:6333/dashboard
```

## 2. Create a virtual environment

**Windows (PowerShell):**
```powershell
py -m venv .venv
.\.venv\Scripts\activate
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

If you plan to upload PDFs through the API (`POST /api/v1/ingest`), also install:
```bash
pip install python-multipart
```
(FastAPI needs it to parse file uploads; it's not in requirements.txt but the
`/ingest` endpoint will fail without it.)

## 4. Configure API keys

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
GOOGLE_API_KEY=your-google-ai-studio-key
GROQ_API_KEY=your-groq-key
```

Leave `QDRANT_URL`, `DATA_DIR`, `ARTIFACT_DIR` etc. at their defaults unless
you have a reason to change them.

## 5. Verify everything is wired up

```bash
python -m app.llm
```

This checks: both API keys are present, Qdrant is reachable, the embedding
model responds, and every model in the fallback chain answers a text and
(where supported) a vision prompt. Fix anything marked `FAIL` before continuing.

## 6. Add PDFs to ingest

Drop PDF files into `data/` (two sample PDFs — `AWS Notes.pdf` and
`Operating_AI-agents.pdf` — are already there). Or add your own.

## 7. Ingest — turn PDFs into chunks + images on disk

```bash
python run_ingest.py                    # every PDF in data/
python run_ingest.py data/MyDoc.pdf     # just one file
```

This writes to `artifacts/<doc_id>/`:
- `page_XXX.png` — a full render of every page
- `page_XXX_img_NN.png` — individual figures extracted from text pages
- `enriched.json` — cached vision-model output (captions/descriptions), so
  re-running never re-spends API quota
- `manifest.json` — every chunk produced, in one file

No vector store is touched at this step — it's pure parsing, safe to re-run.

## 8. Index — embed chunks and load them into Qdrant

```bash
python run_index.py              # index every manifest in artifacts/
python run_index.py --recreate   # wipe the collection first, then index
```

Ingest and index are separate on purpose: you can rebuild the vector index
without re-parsing PDFs or re-calling the vision model.

## 9. Run the API

```bash
uvicorn api:app --reload
```

- Docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## 10. Run the chat UI

In a second terminal (with the venv activated):

```bash
streamlit run ui.py
```

This opens a browser chat UI at `http://localhost:8501` that talks to the API,
lets you upload new PDFs, ask questions, and see cited pages/figures.

## 11. (Optional) Run tests and eval

**Smoke test** (needs the API running and at least one document indexed):
```bash
python tests/smoke_test.py
```

**Retrieval quality eval** (no LLM calls, scores against `eval/golden.jsonl`):
```bash
python eval/run_eval.py
python eval/run_eval.py --compare   # dense vs bm25 vs hybrid
```

**Inspect a PDF before ingesting** (is it a real text layer or a scan?):
```bash
python scripts/probe_pdf.py data/YourFile.pdf
```

## Everyday commands (after first-time setup)

```bash
docker compose up -d               # start Qdrant if not running
.\.venv\Scripts\activate           # or: source .venv/bin/activate
python run_ingest.py               # ingest any new PDFs in data/
python run_index.py                # index them
uvicorn api:app --reload           # terminal 1
streamlit run ui.py                # terminal 2
```

## Troubleshooting

- **`Nothing indexed yet. Run: py run_index.py`** — you ran the API/UI before
  running `run_index.py`, or the collection was recreated. Run step 8.
- **429 / rate limit errors during ingest** — expected on the free tier. The
  pipeline retries and throttles automatically (`GEMINI_SLEEP_SECONDS`,
  `EMBED_SLEEP_SECONDS` in `.env`); just let it run.
- **Qdrant unreachable** — confirm `docker compose ps` shows `mmrag-qdrant`
  as running, and `QDRANT_URL` in `.env` matches (`http://localhost:6333`).
- **Ingest is slow** — this is by design on the free tier: every vision-model
  call is deliberately paced to stay under rate limits. Re-runs are fast
  because `enriched.json` caches every vision call per document.
- **`/ingest` endpoint fails on file upload** — install `python-multipart`
  (see step 3).
