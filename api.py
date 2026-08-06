r"""
FastAPI surface.

Deliberately thin: every endpoint is a few lines that call into app/.
No pipeline logic lives here, which means the whole system stays testable
from a plain Python prompt without starting a server.

    .\.venv\Scripts\uvicorn.exe api:app --reload

    docs   http://localhost:8000/docs
    health http://localhost:8000/health
"""

import shutil
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, retrieve, store
from app.answer import answer_question
from app.ingest import ingest_pdf, slugify

app = FastAPI(title=config.PROJECT_NAME, version="0.1.0")

# The extracted PNGs are served straight off disk. This is what turns an
# image_path in a payload into something a browser can render.
app.mount("/artifacts", StaticFiles(directory=config.ARTIFACT_DIR), name="artifacts")

# Ingestion takes minutes (vision calls are throttled for the free tier), so it
# runs in the background and the caller polls. In-memory is fine for one user.
JOBS = {}


class QueryRequest(BaseModel):
    question: str
    top_text: int | None = None
    top_images: int | None = None
    attach_images: bool = True


def to_url(image_path):
    """Absolute disk path -> a URL the browser can fetch."""
    if not image_path:
        return None
    try:
        relative = Path(image_path).resolve().relative_to(config.ARTIFACT_DIR.resolve())
    except ValueError:
        return None
    return "/artifacts/" + relative.as_posix()


# ----------------------------------------------------------------------
@app.get("/health")
def health():
    try:
        points = store.count()
        qdrant_ok = True
    except Exception as err:
        points, qdrant_ok = 0, f"unreachable: {err}"

    return {
        "status": "ok" if qdrant_ok is True else "degraded",
        "qdrant": {"url": config.QDRANT_URL, "reachable": qdrant_ok, "points": points},
        "collection": config.QDRANT_COLLECTION,
        "llm_chain": [f"{p}:{m}" + ("" if sees else " (text only)")
                      for p, m, sees in config.LLM_CHAIN],
        "embed_model": f"{config.EMBED_MODEL} ({config.EMBED_DIM}d)",
    }


@app.get(config.API_V1_STR + "/documents")
def documents():
    """What has been ingested, read straight off the artifact folder."""
    out = []
    for manifest in sorted(config.ARTIFACT_DIR.glob("*/manifest.json")):
        import json
        data = json.loads(manifest.read_text(encoding="utf-8"))
        out.append({
            "doc_id": data["doc_id"],
            "source": data["source"],
            "pages": data["pages"],
            "chunks": len(data["chunks"]),
            "images": data.get("images", 0),
            "vision_pages": data.get("vision_pages", 0),
        })
    return {"documents": out}


@app.post(config.API_V1_STR + "/query")
def query(request: QueryRequest):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="question is empty")

    try:
        result = answer_question(
            request.question,
            top_text=request.top_text,
            top_images=request.top_images,
            attach_images=request.attach_images,
        )
    except RuntimeError as err:
        # nothing indexed, or every provider in the chain failed
        raise HTTPException(status_code=503, detail=str(err))

    # Swap disk paths for URLs so the UI can render the pictures.
    for image in result["images"]:
        image["url"] = to_url(image.pop("path"))
    return result


# ----------------------------------------------------------------------
def _run_ingest(job_id, pdf_path):
    try:
        import fitz
        with fitz.open(pdf_path) as probe:
            JOBS[job_id]["pages_total"] = probe.page_count

        JOBS[job_id]["status"] = "parsing"
        result = ingest_pdf(pdf_path)

        JOBS[job_id]["status"] = "indexing"
        store.index_chunks(result.chunks, verbose=False)

        # The BM25 index lives in memory and is built once. Without this the
        # newly indexed chunks are invisible to keyword search until the
        # process restarts - a silent half-failure where dense search finds
        # the new document but exact-term search does not.
        retrieve.load_index(force=True)

        JOBS[job_id].update(
            status="done",
            doc_id=result.doc_id,
            pages=result.pages,
            chunks=len(result.chunks),
            images=result.images,
        )
    except Exception as err:
        JOBS[job_id].update(status="failed", error=f"{type(err).__name__}: {err}")


@app.post(config.API_V1_STR + "/ingest")
def ingest(background: BackgroundTasks, file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="only .pdf files are accepted")

    destination = config.DATA_DIR / Path(file.filename).name
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {
        "status": "queued",
        "file": destination.name,
        "doc_id": slugify(destination.stem),
    }
    background.add_task(_run_ingest, job_id, destination)

    return {"job_id": job_id, "status": "queued", "file": destination.name}


def _pages_done(job):
    """
    Count rendered pages on disk so a long ingest reports real progress.

    A 69-page document can take half an hour on the free tier, and a status
    that just says "parsing" the whole time is indistinguishable from a hang.
    """
    if job.get("status") != "parsing" or not job.get("doc_id"):
        return None
    folder = config.ARTIFACT_DIR / job["doc_id"]
    if not folder.exists():
        return 0
    return len([p for p in folder.glob("page_*.png") if "_img_" not in p.name])


@app.get(config.API_V1_STR + "/jobs/{job_id}")
def job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="unknown job")

    job = dict(JOBS[job_id])
    done = _pages_done(job)
    if done is not None:
        job["pages_done"] = done
        total = job.get("pages_total")
        job["progress"] = f"{done}/{total}" if total else str(done)

    return {"job_id": job_id, **job}
