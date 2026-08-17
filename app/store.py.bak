"""
Everything that touches Qdrant.

One collection holds every chunk - text and images together. They are told
apart by the `kind` field in the payload, which is indexed so filtering by
it is fast. Keeping them in one collection is what lets a single query see
both modalities and rank them against each other.

The PNG files never go into Qdrant. The payload stores `image_path` and the
files stay on disk.
"""

import json
import time
import uuid

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from app import config
from app.llm import embed
from app.models import Chunk

_client = QdrantClient(url=config.QDRANT_URL, timeout=60)

# Qdrant point ids must be an int or a UUID, but our chunk ids are readable
# strings like "operating-ai-agents:p8:t1". We hash the string into a stable
# UUID and keep the original in the payload.
_NAMESPACE = uuid.UUID("6f1c9d0e-3f5a-4b2e-9a77-0d5b1c2e3f40")


def point_id(chunk_id):
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


# ----------------------------------------------------------------------
# collection lifecycle
# ----------------------------------------------------------------------
def ensure_collection(recreate=False):
    name = config.QDRANT_COLLECTION

    if recreate and _client.collection_exists(name):
        _client.delete_collection(name)

    if not _client.collection_exists(name):
        _client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(
                size=config.EMBED_DIM,
                distance=qm.Distance.COSINE,
            ),
        )
        # Indexed payload fields = fast filtering. `kind` powers the image quota.
        for field in ("kind", "doc_id"):
            _client.create_payload_index(
                collection_name=name,
                field_name=field,
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
        print(f"[store] created collection '{name}' ({config.EMBED_DIM} dims, cosine)")

    return name


def count():
    if not _client.collection_exists(config.QDRANT_COLLECTION):
        return 0
    return _client.count(config.QDRANT_COLLECTION, exact=True).count


# ----------------------------------------------------------------------
# writing
# ----------------------------------------------------------------------
def index_chunks(chunks, batch_size=None, verbose=True):
    """
    Embed chunks and upsert them. Safe to re-run: point ids are a deterministic
    hash of the chunk id, so a repeat run overwrites rather than duplicating.

    Paced deliberately. The free tier allows ~100 embedded items per minute,
    and each text in a batch counts individually - so a few hundred chunks
    pushed at full speed hits a 429 within seconds. Sleeping between batches
    is far cheaper than retrying after the fact.
    """
    ensure_collection()
    batch_size = batch_size or config.EMBED_BATCH_SIZE
    pause = config.EMBED_SLEEP_SECONDS
    total = len(chunks)

    for start in range(0, total, batch_size):
        batch = chunks[start:start + batch_size]
        vectors = embed([c.text for c in batch])

        _client.upsert(
            collection_name=config.QDRANT_COLLECTION,
            points=[
                qm.PointStruct(id=point_id(c.id), vector=v, payload=c.to_dict())
                for c, v in zip(batch, vectors)
            ],
        )
        done = min(start + batch_size, total)
        if verbose:
            print(f"[store] indexed {done}/{total}")

        if done < total and pause:
            time.sleep(pause)

    return total


def load_manifest(manifest_path):
    """Read chunks back out of an ingest manifest."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return [Chunk(**c) for c in data["chunks"]]


def load_all_manifests():
    chunks = []
    for manifest in sorted(config.ARTIFACT_DIR.glob("*/manifest.json")):
        found = load_manifest(manifest)
        print(f"[store] {manifest.parent.name}: {len(found)} chunks")
        chunks.extend(found)
    return chunks


# ----------------------------------------------------------------------
# reading
# ----------------------------------------------------------------------
def all_payloads():
    """Every payload in the collection - used to build the BM25 index."""
    out, offset = [], None
    while True:
        points, offset = _client.scroll(
            collection_name=config.QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        out.extend(p.payload for p in points)
        if offset is None:
            break
    return out


def dense_search(vector, limit, kind=None):
    """Vector similarity. Returns [(payload, score), ...] best first."""
    query_filter = None
    if kind:
        query_filter = qm.Filter(must=[
            qm.FieldCondition(key="kind", match=qm.MatchValue(value=kind))
        ])

    hits = _client.query_points(
        collection_name=config.QDRANT_COLLECTION,
        query=vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    ).points

    return [(h.payload, h.score) for h in hits]
