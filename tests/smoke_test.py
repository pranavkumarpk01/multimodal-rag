r"""
End-to-end smoke test. Run it against a live API.

    .\.venv\Scripts\python.exe tests\smoke_test.py

Exits 0 if everything passes, 1 otherwise - so it also works in CI.
Each case prints what it checked and why that check matters.
"""

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API = "http://localhost:8000"
V1 = "/api/v1"

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}")
    if detail:
        print(f"        {detail}")


def ask(question, **kwargs):
    body = {"question": question, **kwargs}
    started = time.time()
    response = requests.post(f"{API}{V1}/query", json=body, timeout=300)
    response.raise_for_status()
    data = response.json()
    data["_seconds"] = round(time.time() - started, 1)
    return data


# ======================================================================
def test_health():
    print("\n[1] HEALTH - is every dependency reachable?")
    r = requests.get(f"{API}/health", timeout=30).json()
    check("API responds", r.get("status") == "ok", f"status={r.get('status')}")
    check("Qdrant reachable", r["qdrant"]["reachable"] is True)
    check("Index is populated", r["qdrant"]["points"] > 0,
          f"{r['qdrant']['points']} points in '{r['collection']}'")
    check("Fallback chain has >1 model", len(r["llm_chain"]) > 1,
          " -> ".join(r["llm_chain"]))


def test_documents():
    print("\n[2] DOCUMENTS - is anything ingested?")
    docs = requests.get(f"{API}{V1}/documents", timeout=30).json()["documents"]
    check("At least one document", len(docs) > 0,
          ", ".join(f"{d['doc_id']} ({d['pages']}p, {d['chunks']} chunks)" for d in docs))
    return docs


def test_semantic():
    print("\n[3] SEMANTIC - can it match on meaning, not words?")
    r = ask("How should tools be defined so they fail predictably?")
    check("Got an answer", len(r["answer"]) > 80, f"{len(r['answer'])} chars")
    check("Cited at least one page", len(r["used_pages"]) > 0, f"pages {r['used_pages']}")
    check("Answer mentions schema or contract",
          any(w in r["answer"].lower() for w in ("schema", "contract", "validate")),
          r["answer"][:110])
    print(f"        model={r['model']}  {r['_seconds']}s")


def test_exact_identifier():
    print("\n[4] EXACT MATCH - can BM25 find a string that was only pixels?")
    r = ask("issue_refund ORD pattern maximum 5000")
    sources = r.get("text_sources", [])
    found_by = {tuple(s["found_by"]) for s in sources}
    check("Retrieved something", len(sources) > 0)
    check("BM25 contributed", any("bm25" in s["found_by"] for s in sources),
          f"found_by seen: {found_by}")
    check("Landed on the code-example page",
          any(s["page"] in (8, 9) for s in sources),
          f"pages: {sorted({s['page'] for s in sources})}")


def test_image_retrieval():
    print("\n[5] IMAGES - does a visual question return the actual file?")
    r = ask("Which figure shows the prototype to production journey?")
    images = r.get("images", [])
    check("Images returned", len(images) > 0, f"{len(images)} images")
    check("At least one was cited by the model",
          any(i["cited_by_model"] for i in images),
          ", ".join(f"{i['label']}(p{i['page']}) cited={i['cited_by_model']}" for i in images))

    if images:
        url = API + images[0]["url"]
        head = requests.get(url, timeout=60)
        check("Image file actually serves over HTTP",
              head.status_code == 200 and head.headers.get("Content-Type") == "image/png",
              f"{images[0]['url']} -> {head.status_code}, {len(head.content):,} bytes")


def test_text_only_fallback():
    print("\n[6] FALLBACK - is the answer still grounded without vision?")
    r = ask("Which figure shows the prototype to production journey?",
            attach_images=False)
    check("Answered without seeing images", r["saw_images"] is False)
    check("Answer is still substantive", len(r["answer"]) > 80, f"{len(r['answer'])} chars")
    check("Same image files still returned", len(r.get("images", [])) > 0,
          "retrieval picked them; only the model's view changed")


def test_diagram():
    print("\n[7] DIAGRAM - can it draw a new one on request?")
    r = ask("Draw a flowchart of the stages from prototype to production")
    d = r.get("diagram")
    check("Intent routed to 'draw'", r.get("intent") == "draw", f"intent={r.get('intent')}")
    check("Diagram produced", d is not None)
    if d:
        check("Output is valid-looking DOT",
              "digraph" in d["source"] and d["source"].count("{") == d["source"].count("}"),
              f"{len(d['source'])} chars, title='{d['title']}'")
        check("Marked as generated, not retrieved", d["origin"] == "generated")


def test_no_diagram_on_normal_question():
    print("\n[8] INTENT - does an ordinary question skip the drawing branch?")
    r = ask("What are the core engineering building blocks?")
    check("Intent stayed 'find'", r.get("intent") == "find")
    check("No diagram generated", r.get("diagram") is None)


def test_out_of_scope():
    print("\n[9] OUT OF SCOPE - does it refuse to invent an answer?")
    r = ask("What is the boiling point of liquid nitrogen in Kelvin?")
    lowered = r["answer"].lower()
    admits = any(p in lowered for p in
                 ("not", "no ", "does not", "cannot", "unrelated", "outside", "n/a"))
    check("Declines rather than hallucinating", admits, r["answer"][:150])


def test_bad_request():
    print("\n[10] ERRORS - are bad inputs rejected cleanly?")
    r = requests.post(f"{API}{V1}/query", json={"question": "   "}, timeout=30)
    check("Empty question -> 400", r.status_code == 400, f"got {r.status_code}")

    r = requests.get(f"{API}{V1}/jobs/does-not-exist", timeout=30)
    check("Unknown job -> 404", r.status_code == 404, f"got {r.status_code}")


# ======================================================================
def main():
    print("=" * 70)
    print("MULTIMODAL RAG - SMOKE TEST")
    print("=" * 70)

    try:
        requests.get(f"{API}/health", timeout=10)
    except Exception:
        sys.exit(f"\nAPI is not running at {API}\n"
                 f"Start it with:  .\\.venv\\Scripts\\uvicorn.exe api:app --reload\n")

    for test in (test_health, test_documents, test_semantic, test_exact_identifier,
                 test_image_retrieval, test_text_only_fallback, test_diagram,
                 test_no_diagram_on_normal_question, test_out_of_scope,
                 test_bad_request):
        try:
            test()
        except Exception as err:
            FAILED.append(test.__name__)
            print(f"  ERROR {test.__name__}: {type(err).__name__}: {err}")

    print("\n" + "=" * 70)
    print(f"PASSED {len(PASSED)}   FAILED {len(FAILED)}")
    if FAILED:
        print("failed: " + ", ".join(FAILED))
    print("=" * 70)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
