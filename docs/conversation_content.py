# -*- coding: utf-8 -*-
"""
Structured transcript of the Multimodal RAG design conversation.

Block format:  (kind, payload)

  cover     dict(title, subtitle, meta=[(k, v), ...])
  h1/h2/h3  str
  p         str        - supports **bold** and `inline code`
  bullets   [str]
  numbers   [str]
  code      str        - monospace, light background
  diagram   (title, body)
  table     (headers[], rows[][])
  callout   (kind, title, body)     kind: info | key | warn | tip
  turn      (role, label, subtitle) role: user | assistant | system
  kv        [(key, value)]
  rule      None
  pagebreak None
"""

PROJECT_PATH = "E:\\Rag_App\\multimodal-rag"

BLOCKS = [

    # ================================================================
    ("cover", {
        "title": "Multimodal RAG Pipeline",
        "subtitle": "Design Conversation & Architecture Record",
        "meta": [
            ("Project", PROJECT_PATH),
            ("Scope", "End-to-end RAG over complex PDFs returning text answers + exact images"),
            ("Stack", "PyMuPDF - Claude Opus 5 - Voyage AI - Qdrant - FastAPI - Streamlit"),
            ("Status", "Design approved, implementation pending"),
            ("Document", "Full conversation transcript with responses"),
        ],
    }),

    ("h1", "Contents"),
    ("numbers", [
        "Turn 1 - Project request and design overview",
        "Design decisions (stack selection)",
        "Confirmed stack and Phase 1 plan",
        "Turn 2 - Step-by-step explanation of how it works",
        "Turn 3 - Block diagrams with explanation",
        "Turn 4 - Document generation request",
        "Appendix - Quick reference",
    ]),

    ("pagebreak", None),

    # ================================================================
    # TURN 1
    # ================================================================
    ("turn", ("user", "Turn 1 - User", "The original project request")),

    ("p", "E:\\Rag_App\\multimodal-rag  Please access this folder. Before starting implementing "
          "the project, give me overview of designing and implement an end-to-end Multimodal RAG "
          "(Retrieval-Augmented Generation) pipeline capable of ingesting complex PDFs "
          "(containing text, embedded images, diagrams, and tables) and returning both "
          "synthesized text answers and the exact retrieved images to the user."),

    ("turn", ("assistant", "Turn 1 - Response", "Folder inspection and full design overview")),

    ("h2", "Current state of the project folder"),
    ("p", "The folder is an empty scaffold: `.env` (empty), `.gitignore` (containing `.env`, "
          "`__pycache__/`, `extracted_images/`), an empty `requirements.txt`, and a git repository "
          "with zero commits. This is greenfield. Python is implied by the gitignore, so the "
          "assumption is Python plus Claude via the `anthropic` SDK."),

    ("h2", "1. The core problem"),
    ("p", "A normal RAG pipeline flattens a PDF into text and throws away everything that carries "
          "the actual information in technical documents: architecture diagrams, plots, screenshots "
          "and tables. This requirement adds a second twist - the answer must return **the exact "
          "retrieved image**, not a description of it. That means images are first-class retrievable "
          "objects with stable IDs, not decoration."),
    ("p", "Three sub-problems fall out of this:"),

    ("table", (
        ["Sub-problem", "Why it is hard"],
        [
            ["Extraction",
             "PDFs have no semantic structure. Images are XObjects with no captions attached; "
             "tables are just lines and text runs."],
            ["Cross-modal retrieval",
             "A text query (\"show me the auth flow\") must match a picture. Text and image "
             "embeddings must live in a comparable space - or you sidestep it entirely."],
            ["Grounded generation",
             "Claude must answer from retrieved text and images, then cite which image it used "
             "so the exact file can be returned."],
        ],
    )),

    ("h2", "2. Architecture"),
    ("diagram", ("High-level flow", r"""
                    +------------ INGESTION (offline) ------------+
  PDF --> Parser --> Text blocks ---+
            |        Images  -------+--> Enricher --> Chunker --> Embedder --> Vector DB
            |        Tables  -------+    (Claude       (layout-    (dense +      + object
            |                            vision)        aware)      sparse)        store
            +--> Page renders (PNG @ 200dpi)

                    +------------ QUERY (online) ------------+
  Question --> Embed --> Hybrid search --> Rerank --> Assemble context --> Claude Opus 5
                                                          |                     |
                                                    text chunks +          answer + which
                                                    image blocks           image IDs it used
                                                                                 |
                                            Response: { answer, citations, images[] }
""".strip("\n"))),

    ("h2", "3. Stage-by-stage design"),

    ("h3", "3.1 Parsing - PyMuPDF as the spine"),
    ("p", "PyMuPDF (fitz) is the right primary tool - it gives text with bounding boxes, embedded "
          "image extraction, and page rasterisation from one library."),
    ("bullets", [
        "**Text** - `page.get_text(\"dict\")` returns blocks with bbox, font size and span info. "
        "Font size drives heading detection, which drives section-aware chunking.",
        "**Images** - `page.get_images()` extracts raw XObjects. Filter aggressively: drop anything "
        "under ~10k pixels, drop images with under 5% colour variance (logos, rules, background "
        "fills). Unfiltered extraction is the number one source of index pollution.",
        "**Tables** - `page.find_tables()` (PyMuPDF 1.23+) handles ruled tables well. For borderless "
        "tables it fails, and the fallback is to crop the region and let Claude vision transcribe "
        "it to Markdown.",
        "**Page renders** - rasterise every page at ~200 DPI. These are the safety net: if extraction "
        "misses a diagram, the page image still contains it, and they enable the ColPali-style "
        "fallback.",
    ]),
    ("p", "Optional upgrade: `unstructured` or a layout model (LayoutParser / DocLayNet) for cleaner "
          "region segmentation. Recommended to start without it - it is a heavy dependency and "
          "PyMuPDF covers most technical PDFs."),

    ("h3", "3.2 Enrichment - the key design decision"),
    ("p", "A raw image has no text to embed. You have to give it text. For each extracted image or "
          "table, run one Claude vision call that produces:"),
    ("code", """{
  "caption": "Sequence diagram of the OAuth2 authorization-code flow",
  "description": "Detailed prose: actors, arrows, labels, axis values...",
  "extracted_text": "all text visible inside the figure",
  "type": "diagram | chart | screenshot | table | photo",
  "keywords": ["oauth2", "pkce", "token exchange"]
}"""),
    ("p", "Feed it the image plus surrounding page text (caption line, preceding paragraph) so it can "
          "resolve \"Figure 4\" references. Use structured outputs (`output_config.format`) so this is "
          "a validated object, not prose you have to regex. This is a batch job - run it through the "
          "Message Batches API at 50% cost, or Haiku 4.5 for simple figures with Opus 5 for dense "
          "diagrams."),
    ("callout", ("key", "The pivot of the design",
                 "The enriched text becomes the searchable surface; the image file itself is the "
                 "payload you return.")),

    ("h3", "3.3 Retrieval strategy - three options"),
    ("table", (
        ["", "A. Summary-indexed (chosen)", "B. Unified multimodal", "C. Page-level visual"],
        [
            ["How",
             "Embed the Claude-generated description; store image path as payload",
             "Embed image pixels and text into one shared space (voyage-multimodal-3)",
             "Embed whole page images as multi-vector patches; retrieve pages, not chunks"],
            ["Pros",
             "Cheap, debuggable, works with any text embedder, great recall on semantic queries",
             "True cross-modal, no LLM enrichment cost, catches visual similarity",
             "Zero parsing, no extraction bugs, state of the art on visually dense documents"],
            ["Cons",
             "Quality capped by caption quality; enrichment cost at ingest",
             "Weaker on abstract queries; less mature tooling",
             "Heavy index, needs GPU, cannot return sub-page crops"],
            ["Verdict",
             "Start here. Ship it, measure it.",
             "Add later as a second retriever and fuse - genuinely complements A",
             "Only for slide decks or scanned reports"],
        ],
    )),
    ("callout", ("warn", "Anthropic has no embeddings endpoint",
                 "Claude handles enrichment and generation; embeddings come from Voyage AI "
                 "(voyage-3-large for text, voyage-multimodal-3 for option B) or a local bge/e5 "
                 "model if you want zero external calls.")),
    ("p", "On top of whichever option is picked:"),
    ("bullets", [
        "**Hybrid search** - dense plus BM25/SPLADE, fused with RRF. Technical documents are full of "
        "exact identifiers (`ERR_TOKEN_EXPIRED`, `Fig. 4.2`) that dense retrieval alone misses.",
        "**Reranking** - voyage-rerank-2 or a cross-encoder over the top 50. The single highest-ROI "
        "addition to a RAG pipeline; expect a large precision jump.",
        "**Modality quotas** - do not let 20 text chunks crowd out every image. Reserve slots "
        "(for example top-8 text plus top-3 images) rather than taking a flat top-k.",
        "**Parent-document expansion** - retrieve on small chunks, send the enclosing section to "
        "the model.",
    ]),

    ("h3", "3.4 Generation"),
    ("p", "Build the Claude message as interleaved content blocks:"),
    ("code", """[text: "Context from document X, page 12:"]
[text: <chunk>]
[text: "Figure F-0042 (page 12):"]
[image: <base64 or file_id of the actual image>]   <-- Claude SEES it
[text: <question>]"""),
    ("p", "Claude Opus 5 with adaptive thinking. Key mechanics:"),
    ("bullets", [
        "**Citations** (`citations: {enabled: true}` on document blocks) return `page_location` "
        "automatically - grounded answers without prompt-engineering a citation format.",
        "**Prompt caching** on the system prompt and fixed instructions. Cache retrieved context too "
        "if the same document is queried repeatedly in a session.",
        "**Structured output** for the final envelope so `used_image_ids` is machine-readable: that "
        "array drives which images the API returns.",
        "**Streaming** - retrieval plus vision plus generation is a multi-second operation; stream so "
        "the UI is not dead.",
    ]),

    ("h3", "3.5 Response contract"),
    ("code", """{
  "answer": "The auth flow uses PKCE. As shown in Figure 4...",
  "citations": [{"doc": "spec.pdf", "page": 12, "text": "..."}],
  "images": [
    {"id": "F-0042", "url": "/artifacts/spec/p12_img01.png",
     "page": 12, "bbox": [x0,y0,x1,y1], "caption": "OAuth2 sequence diagram",
     "score": 0.83, "cited_by_model": true}
  ]
}"""),
    ("p", "`cited_by_model` matters: return everything retrieved (so the user can see near-misses) "
          "but flag what the answer actually leaned on."),

    ("h2", "4. Proposed repository structure"),
    ("diagram", ("Repository layout", r"""
multimodal-rag/
+-- app/
|   +-- ingest/        parser.py  extractor.py  enricher.py  chunker.py
|   +-- index/         embedder.py  store.py  bm25.py
|   +-- retrieve/      hybrid.py  rerank.py  fusion.py
|   +-- generate/      context.py  claude.py  prompts.py
|   +-- api/           main.py  routes.py  schemas.py
|   +-- core/          config.py  models.py  logging.py
+-- artifacts/         {doc_id}/page_{n}_img_{k}.png   (served statically)
+-- data/              raw PDFs
+-- eval/              golden_qa.jsonl  run_eval.py
+-- scripts/           ingest_pdf.py
+-- tests/
""".strip("\n"))),
    ("p", "**Endpoints:** `POST /ingest` (async, returns job id), `GET /ingest/{job}`, "
          "`POST /query` (SSE stream), `GET /artifacts/{doc}/{file}`, `GET /health`."),
    ("p", "**Dependencies:** anthropic, voyageai, pymupdf, qdrant-client, rank-bm25, fastapi, "
          "uvicorn, pydantic-settings, pillow, tenacity."),

    ("h2", "5. Build phases"),
    ("numbers", [
        "**Extraction spike** - one PDF in, text plus filtered images plus tables on disk with "
        "metadata JSON. Eyeball the output; extraction quality determines everything downstream.",
        "**Enrichment and indexing** - Claude vision descriptions, embed, load into the store.",
        "**Retrieval** - hybrid plus rerank plus modality quotas. Test with hand-written queries "
        "before wiring the LLM.",
        "**Generation** - context assembly, Claude call, structured response.",
        "**API and streaming.**",
        "**Eval harness** - 30 to 50 hand-labelled Q/A pairs with expected image IDs. Track recall@k, "
        "image-hit rate and answer faithfulness. Without this you are tuning blind.",
        "**Hardening** - caching, batch ingest, dedup, cost telemetry.",
    ]),

    ("h2", "6. Risks worth naming now"),
    ("bullets", [
        "**Extraction is where projects die.** Scanned PDFs need OCR (Tesseract or Claude vision on "
        "page renders). Vector-graphic diagrams are not XObjects at all - they are draw commands, and "
        "only page rasterisation catches them.",
        "**Caption quality is the retrieval ceiling** in approach A. Budget prompt-engineering time on "
        "the enricher, and evaluate captions directly.",
        "**Image token cost.** Full-resolution images on Opus 5 run up to ~4,800 tokens each. Cap "
        "images per answer (3 to 5) and downsample where fidelity is not needed.",
        "**Table fidelity.** Merged cells and multi-page tables break naive extraction. Markdown/HTML "
        "transcription via vision is more robust than coordinate-based reconstruction.",
        "**`.env` is empty** - `ANTHROPIC_API_KEY` plus an embedding provider key are needed before "
        "anything runs.",
    ]),

    ("pagebreak", None),

    # ================================================================
    # DECISIONS
    # ================================================================
    ("turn", ("system", "Design Decisions", "Three questions asked, three answers given")),

    ("table", (
        ["Question", "Options offered", "Decision"],
        [
            ["Which retrieval strategy first?",
             "Summary-indexed / Unified multimodal / Both fused / Page-level visual",
             "**Summary-indexed**"],
            ["Where should vectors and artifacts live?",
             "Qdrant local / Supabase pgvector / ChromaDB",
             "**Qdrant local**"],
            ["What should the delivery surface be?",
             "FastAPI only / FastAPI + Streamlit / CLI only",
             "**FastAPI + Streamlit UI**"],
        ],
    )),

    ("turn", ("assistant", "Response", "Stack locked and Phase 1 scope")),

    ("h2", "Stack locked"),
    ("table", (
        ["Layer", "Choice"],
        [
            ["Parsing", "PyMuPDF (text + bbox, image XObjects, find_tables, 200dpi page renders)"],
            ["Enrichment", "Claude vision - structured caption / description / extracted-text per image and table"],
            ["Embeddings", "Voyage voyage-3-large (text) - Anthropic has no embeddings endpoint"],
            ["Vector store", "Qdrant via Docker, named vectors + payload filtering, native sparse for hybrid"],
            ["Rerank", "voyage-rerank-2 over top-50"],
            ["Generation", "claude-opus-5, adaptive thinking, streaming, prompt caching, structured output for used_image_ids"],
            ["Artifacts", "Local disk under artifacts/{doc_id}/, served statically by FastAPI"],
            ["Surface", "FastAPI (REST + SSE) + Streamlit chat UI rendering retrieved images inline"],
        ],
    )),
    ("p", "`requirements.txt` will be: anthropic, voyageai, pymupdf, qdrant-client, fastapi, "
          "uvicorn[standard], streamlit, pydantic-settings, pillow, rank-bm25, tenacity, "
          "python-multipart."),

    ("h2", "Two blockers before anything can run"),
    ("numbers", [
        "**`.env` is empty** - needs `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`. The `.env.example` and "
        "config loader will be written; the keys must be supplied.",
        "**No PDFs anywhere in the project.** Phase 1 is an extraction spike, and extraction quality "
        "against real documents is what determines whether the summary-indexed approach holds up. A "
        "representative PDF (ideally one with diagrams and a borderless table) should go in `data/`.",
    ]),

    ("h2", "Phase 1 scope - scaffold plus extraction spike, no LLM calls"),
    ("bullets", [
        "Package structure, `config.py` (pydantic-settings), `models.py` (the Document / TextBlock / "
        "ImageAsset / TableAsset schemas everything else depends on), `requirements.txt`, `.env.example`",
        "`ingest/parser.py` - page iteration, text blocks with bbox plus font-size heading detection",
        "`ingest/extractor.py` - image XObject extraction with the noise filters (size plus colour "
        "variance), table detection, page rasterisation",
        "`scripts/ingest_pdf.py` - CLI that dumps `artifacts/{doc_id}/` plus a `manifest.json` for "
        "inspection",
    ]),
    ("p", "That gives something inspectable before a single token is spent on enrichment."),

    ("pagebreak", None),

    # ================================================================
    # TURN 2
    # ================================================================
    ("turn", ("user", "Turn 2 - User", "Request for a plain-language walkthrough")),
    ("p", "explain me each steps and it's functioning properly in simple way, then I start to implement"),

    ("turn", ("assistant", "Turn 2 - Response", "How the whole thing works, step by step")),

    ("p", "Walking one PDF through the system, then one question. Running example: "
          "`payments-api-spec.pdf`, 40 pages, with an OAuth sequence diagram on page 12 and a "
          "rate-limit table on page 18."),
    ("callout", ("key", "The one-sentence version",
                 "Ingestion turns a PDF into a searchable library where every paragraph, picture and "
                 "table is a separate labelled item; querying finds the handful of items relevant to a "
                 "question and hands them to Claude to answer from.")),

    ("h1", "Part A - Ingestion (once per PDF, offline)"),

    ("h3", "Step 1 - Parse the pages"),
    ("kv", [
        ("In", "The PDF file."),
        ("What happens", "PyMuPDF opens it and walks page by page. For each page it pulls out text "
                         "with coordinates - not one blob of text, but a list of blocks, each with its "
                         "position on the page and its font size."),
        ("Why it matters", "Font size tells you \"3.2 Token Refresh\" is a heading and the paragraph "
                           "under it is body text. Position tells you which caption sits under which "
                           "picture. Plain text extraction throws both away, and then you cannot tell "
                           "a heading from a footnote."),
        ("Out", "A list like {text: \"3.2 Token Refresh\", page: 12, bbox: [72, 90, 300, 108], "
                "font_size: 14}."),
        ("Healthy when", "Headings are detected as headings, and reading order is sane on two-column "
                         "pages."),
    ]),

    ("h3", "Step 2 - Extract the pictures"),
    ("p", "PDFs store images as embedded objects. PyMuPDF pulls each one out as a real PNG file and "
          "records which page it came from and where on that page it sat."),
    ("p", "Then the filtering, which matters more than the extraction: a 40-page PDF might yield 200 "
          "\"images\", of which 180 are the company logo repeated in every header, horizontal rule "
          "lines, and background shading. Two filters cut them:"),
    ("bullets", [
        "**Size** - anything under ~10,000 pixels is decoration.",
        "**Colour variance** - an image that is 98% one colour is a rule, a fill, or a bullet, "
        "not a diagram.",
    ]),
    ("p", "Separately, every page is also photographed as a whole-page PNG at 200 DPI. That is a "
          "safety net: some diagrams are not stored as images at all, they are drawing instructions "
          "(lines and shapes), so extraction finds nothing - but the page photo still contains "
          "the diagram."),
    ("kv", [
        ("Out", "artifacts/payments-api-spec/page12_img01.png - the actual OAuth diagram, on disk, "
                "with a stable ID."),
        ("Healthy when", "You open the artifacts folder and see ~15 real figures, not 200 logos."),
    ]),

    ("h3", "Step 3 - Find the tables"),
    ("p", "PyMuPDF detects tables by finding the ruling lines and the grid of text inside them, then "
          "gives you rows and columns. This works well when the table has visible borders. Borderless "
          "tables (just whitespace alignment) defeat it - for those, the page region is cropped as an "
          "image and Claude reads it in the next step."),
    ("kv", [
        ("Healthy when", "The rate-limit table on page 18 comes back with its columns intact, not "
                         "scrambled into one line."),
    ]),

    ("h3", "Step 4 - Describe the pictures (the clever bit)"),
    ("callout", ("warn", "The core problem",
                 "You cannot search a picture. Search works by comparing meaning between pieces of "
                 "text. A PNG has no text in it.")),
    ("p", "So we give each picture text. Every extracted image and table is sent to Claude with "
          "vision, along with the text that surrounds it on the page (the caption line, the paragraph "
          "above it), and Claude writes a description:"),
    ("code", """caption:  "OAuth2 authorization-code flow with PKCE"
description: "Sequence diagram with four actors: Client App, User Agent,
              Auth Server, Resource Server. Shows the initial authorization
              request, the code challenge, token exchange, and the retry
              path when the access token has expired..."
extracted_text: "code_challenge, code_verifier, 401 TOKEN_EXPIRED, /oauth/refresh"
type: "diagram\""""),
    ("p", "Two things to notice:"),
    ("bullets", [
        "The surrounding page text is passed so Claude can resolve references. Without it, a diagram "
        "labelled only \"Figure 4\" is meaningless. With it, Claude knows Figure 4 is the auth flow.",
        "`extracted_text` captures the labels inside the picture. That is how a search for "
        "`TOKEN_EXPIRED` can find a diagram - the string only exists as pixels in the image, but now "
        "it exists as text in the index.",
    ]),
    ("callout", ("key", "From here on",
                 "The picture is represented by its description. The description gets searched; "
                 "the PNG gets returned.")),
    ("kv", [
        ("Healthy when", "You read 10 descriptions cold, without seeing the images, and you can tell "
                         "what each picture shows."),
    ]),

    ("h3", "Step 5 - Chunk the text"),
    ("p", "You cannot index a whole 40-page document as one item - a search would either match "
          "everything or nothing. So it is cut into pieces of roughly 500 to 800 words."),
    ("p", "But the cuts are on **section boundaries**, not every N characters. Chopping mid-sentence "
          "at character 500 splits an explanation across two chunks, and then neither chunk makes "
          "sense on its own. Using the headings from Step 1, each chunk is a coherent unit, and it "
          "carries its heading trail with it: \"3. Authentication > 3.2 Token Refresh\". That context "
          "helps both retrieval and the final answer."),
    ("kv", [
        ("Out", "~120 text chunks + ~15 image descriptions + ~6 table descriptions. About 140 "
                "searchable items, each with a page number and, for images, a path to the PNG."),
    ]),

    ("h3", "Step 6 - Turn everything into numbers (embeddings)"),
    ("p", "Each piece of text goes to Voyage's embedding model, which returns a list of ~1,000 "
          "numbers - a \"vector\". The useful property: **texts with similar meaning produce similar "
          "vectors**. \"how do I refresh an expired token\" and \"token renewal procedure\" land "
          "close together numerically, even though they share almost no words."),
    ("p", "That is what makes semantic search work. Keyword search would miss that pair entirely. "
          "Note that image descriptions get embedded exactly like text chunks - they are just text at "
          "this point. That is the whole trick of summary-indexing."),

    ("h3", "Step 7 - Store it"),
    ("p", "Everything goes into Qdrant. Each stored record holds the vector (for meaning-based "
          "search), the original text (to show the model later), and metadata: `doc_id`, `page`, "
          "`type` (text/image/table), and for images the path to the PNG. The PNG files themselves "
          "stay on disk; Qdrant just stores the path."),
    ("p", "Two indexes get built, not one:"),
    ("bullets", [
        "**Dense** (the vectors) - finds things by meaning.",
        "**BM25 / keyword** - finds exact strings. Technical documents are full of identifiers like "
        "`ERR_TOKEN_EXPIRED` or `Figure 4.2`, and vector search is genuinely bad at exact-token "
        "matching. You need both.",
    ]),
    ("callout", ("info", "Ingestion is done",
                 "The PDF is now a searchable library.")),

    ("h1", "Part B - Answering a question (per query, live)"),
    ("p", "Question: **\"How does the auth flow handle expired tokens?\"**"),

    ("h3", "Step 8 - Embed the question"),
    ("p", "Same model as Step 6 - the question becomes a vector. It has to be the same model, or the "
          "numbers are not comparable."),

    ("h3", "Step 9 - Search, two ways at once"),
    ("bullets", [
        "**Dense search:** find the ~50 stored vectors closest to the question's vector. This catches "
        "the token-refresh section even if it never says \"expired\".",
        "**Keyword search:** find items containing `auth`, `expired`, `token`. This catches the exact "
        "error code.",
    ]),
    ("p", "The two result lists are merged with RRF - items ranked highly by both retrievers float to "
          "the top."),
    ("callout", ("warn", "The modality quota",
                 "Reserve slots by type, for example top 8 text plus top 3 images. Without this, text "
                 "chunks win every time on raw score and images never surface - which would defeat "
                 "the whole point of the project.")),

    ("h3", "Step 10 - Rerank"),
    ("p", "The first search was fast and approximate - it compared pre-computed vectors. Reranking is "
          "slow and precise: a model reads the question and each candidate together and scores actual "
          "relevance."),
    ("p", "This is the single highest-impact step for answer quality. Fast search gets you in the "
          "neighbourhood; reranking picks the right house. A chunk that merely mentions \"token\" "
          "drops; the one that explains the refresh path rises. Output: top ~10, correctly ordered."),

    ("h3", "Step 11 - Build the message for Claude"),
    ("code", """"Context from payments-api-spec.pdf, page 11:"
[text of the token-refresh section]

"Figure F-0042 from page 12:"
[the actual PNG of the OAuth diagram]      <-- Claude sees the real image

"Question: How does the auth flow handle expired tokens?\""""),
    ("callout", ("key", "Important",
                 "Claude receives the real image, not the description. The description was only ever "
                 "a search key to find the image. Once found, the model looks at the diagram itself "
                 "and can read details the caption never mentioned.")),

    ("h3", "Step 12 - Generate"),
    ("p", "Claude Opus 5 reads the text, looks at the images, and answers. Two settings do real work:"),
    ("bullets", [
        "**Citations** - Claude marks which source each claim came from, with page numbers, "
        "automatically.",
        "**Structured output** - the response comes back as a fixed shape including "
        "`used_image_ids: [\"F-0042\"]`, so the code knows which images the answer actually relied "
        "on, rather than guessing.",
    ]),

    ("h3", "Step 13 - Return"),
    ("code", """{
  "answer": "When the access token expires the resource server returns
             401 TOKEN_EXPIRED. The client then calls /oauth/refresh with
             the stored refresh token... (see Figure 4)",
  "citations": [{"page": 11}, {"page": 12}],
  "images": [
    {"id": "F-0042", "url": "/artifacts/payments-api-spec/page12_img01.png",
     "page": 12, "caption": "OAuth2 flow with PKCE", "cited_by_model": true}
  ]
}"""),
    ("p", "The Streamlit UI renders the answer text and displays that PNG inline underneath. "
          "**That image is the exact file extracted from page 12 in Step 2** - same bytes, not a "
          "regeneration, not a description."),

    ("h2", "How you will know each stage is healthy"),
    ("table", (
        ["Stage", "The check", "Bad sign"],
        [
            ["Parse", "Headings detected; two-column pages read in order", "Text scrambled across columns"],
            ["Extract images", "~15 real figures in artifacts/", "200 files, mostly logos"],
            ["Tables", "Columns preserved", "Everything on one row"],
            ["Describe", "Read descriptions blind - can you tell what each shows?", "Vague: \"a diagram with boxes\""],
            ["Chunk", "Each chunk readable standalone", "Sentences cut in half"],
            ["Embed", "\"token refresh\" and \"renew credentials\" score as similar", "Random similarity scores"],
            ["Search", "Query for a known figure - does it come back top-5?", "Right answer at rank 30"],
            ["Rerank", "Top-3 are all genuinely relevant", "Order unchanged from search"],
            ["Generate", "Answer cites the right pages", "Confident answer, wrong pages"],
        ],
    )),
    ("p", "The eval harness (Phase 6) automates the last three: ~40 hand-written questions, each "
          "labelled with the pages and image IDs that should come back. Then any change to chunk "
          "size, filters or prompts is measurable instead of vibes."),

    ("h2", "Where the real difficulty is"),
    ("p", "Not in the retrieval mathematics - that is largely solved libraries. It is in Steps 2 and 4:"),
    ("bullets", [
        "If extraction pulls out garbage, you index garbage. Filters need tuning against your PDFs.",
        "If the descriptions are vague, retrieval is capped no matter how good the embedding model is. "
        "In the summary-indexed design, **caption quality is the ceiling on image recall**. That is "
        "the one prompt worth iterating on properly.",
    ]),
    ("p", "Everything else is plumbing."),

    ("pagebreak", None),

    # ================================================================
    # TURN 3
    # ================================================================
    ("turn", ("user", "Turn 3 - User", "Request for block diagrams")),
    ("p", "give me Explaination with block diagram. Give me block diagram with explaination"),

    ("turn", ("assistant", "Turn 3 - Response", "Five block diagrams with per-block explanation")),

    ("h2", "Diagram 1 - System overview"),
    ("diagram", ("Two pipelines joined only by storage", """
+=======================================================================+
|                       MULTIMODAL RAG SYSTEM                           |
+=======================================================================+

    OFFLINE  (once per PDF)                ONLINE  (once per question)
    =======================                ===========================

        +-----------+                            +-----------+
        |    PDF    |                            |  QUESTION |
        +-----+-----+                            +-----+-----+
              |                                        |
              v                                        v
      +---------------+                        +---------------+
      |   INGESTION   |                        |   RETRIEVAL   |
      |    PIPELINE   |                        |    PIPELINE   |
      |  (blocks 1-8) |                        | (blocks 9-13) |
      +-------+-------+                        +-------+-------+
              |                                        |
              | WRITES                           READS |
              v                                        v
      +===========================================================+
      |                     STORAGE LAYER                         |
      |   +------------------+        +-----------------------+   |
      |   |  QDRANT          |        |  ARTIFACT STORE       |   |
      |   |  - dense vectors | path   |  - page12_img01.png   |   |
      |   |  - sparse index  |------->|  - page18_tbl01.png   |   |
      |   |  - text + meta   |        |  artifacts/{doc_id}/  |   |
      |   +------------------+        +-----------------------+   |
      +===========================================================+
                                                 |
                                                 v
                                       +-------------------+
                                       |   GENERATION      |
                                       |   Claude Opus 5   |
                                       +---------+---------+
                                                 v
                                    +--------------------------+
                                    |  answer + citations      |
                                    |  + THE EXACT IMAGE FILES |
                                    +--------------------------+
""".strip("\n"))),
    ("p", "**Reading it:** two pipelines that never talk to each other directly. They communicate only "
          "through the storage layer. Ingestion is slow, expensive, and runs once. Retrieval is fast "
          "and runs on every question. Qdrant holds searchable numbers; the artifact store holds the "
          "actual PNG bytes you eventually return. Qdrant only stores the file path - it never stores "
          "an image."),

    ("pagebreak", None),

    ("h2", "Diagram 2 - Ingestion pipeline (blocks 1 to 8)"),
    ("diagram", ("Three lanes converging at the enricher", """
                          +---------------+
                          |   PDF FILE    |
                          +-------+-------+
                                  v
        +-----------------------------------------------------+
        |  (1) PARSER                          [PyMuPDF]      |
        |      opens document, walks page by page             |
        +--+-------------+--------------+--------------+------+
           |             |              |              |
    text+bbox      image objects    table grids    page renders
           |             |              |           (200 DPI)
           v             v              v              |
  +-------------+ +-------------+ +-------------+      |
  |(2) TEXT     | |(3) IMAGE    | |(4) TABLE    |      |
  |    BLOCKS   | |    EXTRACTOR| |    DETECTOR |      |
  |             | |             | |             |      |
  | font-size ->| | FILTERS:    | | ruled->rows |      |
  | heading     | |  - size     | | borderless->|      |
  | detection   | |  - colour   | |  crop image |      |
  |             | |    variance | |             |      |
  +------+------+ +------+------+ +------+------+      |
         |               |               |             |
         |               +-------+-------+             |
         |                       |                     |
         |                       v                     |
         |        +--------------------------+         |
         |        | (5) ENRICHER             |<--------+
         |        |     [Claude vision]      | safety-net
         |        |                          | fallback
         |        |  image + page context -> |
         |        |    caption               |
         |        |    description           |
         |        |    extracted_text        |
         |        |    type, keywords        |
         |        +------------+-------------+
         |                     |
         |     <-- PICTURES ARE NOW TEXT -->
         |                     |
         v                     v
  +-----------------------------------------------+
  | (6) CHUNKER                                   |
  |     cut on SECTION boundaries, 500-800 words  |
  |     each chunk carries its heading trail      |
  +-----------------------+-----------------------+
                          v
  +-----------------------------------------------+
  | (7) EMBEDDER                     [Voyage AI]  |
  |     text -> ~1000 numbers (a vector)          |
  |     similar meaning => similar numbers        |
  +-----------------------+-----------------------+
                          v
  +-----------------------------------------------+
  | (8) INDEXER                        [Qdrant]   |
  |     dense index (meaning) + sparse (exact)    |
  +-----------------------------------------------+
""".strip("\n"))),

    ("h3", "What each ingestion block does"),
    ("table", (
        ["#", "Block", "In", "Out", "Job"],
        [
            ["1", "Parser", "PDF", "pages", "Opens the file, exposes text/images/tables/renders per page"],
            ["2", "Text blocks", "raw text runs", "blocks + heading level", "Keeps position and font size so structure survives"],
            ["3", "Image extractor", "embedded objects", "PNG files", "Pulls real images out; filters kill logos and rule lines"],
            ["4", "Table detector", "ruling lines + text grid", "rows, or a crop", "Structured tables when ruled, image fallback when not"],
            ["5", "Enricher", "image + surrounding text", "a text description", "Makes pictures searchable"],
            ["6", "Chunker", "text + descriptions", "~140 chunks", "Right-sized, self-contained units"],
            ["7", "Embedder", "chunk text", "vector", "Converts meaning into comparable numbers"],
            ["8", "Indexer", "vectors + metadata", "populated DB", "Two indexes: semantic and keyword"],
        ],
    )),
    ("callout", ("key", "The critical junction is block 5",
                 "Notice where the three lanes converge: text goes straight down, but images and "
                 "tables have to pass through the enricher first. That is the pivot of the whole "
                 "design - a PNG cannot be searched, so Claude writes text about it, and from block 6 "
                 "onward an image is treated exactly like any other paragraph. The PNG itself sits "
                 "untouched on disk, waiting to be handed back at the very end.")),
    ("p", "Also note the path on the right: page renders bypass blocks 1 to 4 entirely and feed block "
          "5 directly. That is the safety net for diagrams drawn as vector shapes, which produce zero "
          "extractable images."),

    ("pagebreak", None),

    ("h2", "Diagram 3 - Query pipeline (blocks 9 to 15)"),
    ("diagram", ("Dual search, fusion, rerank, generate", """
              +----------------------------------------+
              |  QUESTION                              |
              |  "How does auth handle expired tokens?"|
              +--------------------+-------------------+
                                   v
              +----------------------------------------+
              | (9) QUERY EMBEDDER        [Voyage AI]  |
              |     SAME model as block 7 (must match) |
              +--------------------+-------------------+
                                   |
                   +---------------+---------------+
                   v                               v
       +-----------------------+       +-----------------------+
       | (10a) DENSE SEARCH    |       | (10b) SPARSE SEARCH   |
       |       vector similar. |       |       BM25 keyword    |
       |                       |       |                       |
       |  finds MEANING        |       |  finds EXACT STRINGS  |
       |  "renew credentials"  |       |  "ERR_TOKEN_EXPIRED"  |
       |  ~ "refresh token"    |       |  "Figure 4.2"         |
       +-----------+-----------+       +-----------+-----------+
                   +---------------+---------------+
                                   v
              +----------------------------------------+
              | (11) FUSION (RRF) + MODALITY QUOTA     |
              |                                        |
              |     merge both lists                   |
              |     reserve slots: 8 text : 3 images   |
              |     !! without the quota, text wins    |
              |        every time and images never     |
              |        surface                         |
              +--------------------+-------------------+
                                   |  ~50 candidates
                                   v
              +----------------------------------------+
              | (12) RERANKER           [voyage-rerank]|
              |      reads question + candidate TOGETHER|
              |      slow but precise                  |
              |      <- biggest single quality win     |
              +--------------------+-------------------+
                                   |  top ~10, ordered
                                   v
              +----------------------------------------+
              | (13) CONTEXT ASSEMBLER                 |
              |      loads REAL PNG bytes from disk    |
              |      builds interleaved message:       |
              |        [text] [text] [IMAGE] [text]... |
              +--------------------+-------------------+
                                   v
              +----------------------------------------+
              | (14) CLAUDE OPUS 5                     |
              |      reads text - LOOKS AT images      |
              |      adaptive thinking - streaming     |
              |      citations - structured output     |
              +--------------------+-------------------+
                                   v
              +----------------------------------------+
              | (15) RESPONSE                          |
              |      answer - citations[] - images[]   |
              |      used_image_ids -> what mattered   |
              +----------------------------------------+
""".strip("\n"))),

    ("h3", "What each query block does"),
    ("table", (
        ["#", "Block", "In", "Out", "Job"],
        [
            ["9", "Query embedder", "question text", "vector", "Puts the question in the same number-space as the index"],
            ["10", "Dual search", "query vector + terms", "~50 candidates", "Meaning search and exact-string search, in parallel"],
            ["11", "Fusion + quota", "two ranked lists", "one ranked list", "Merges them; guarantees images get seats at the table"],
            ["12", "Reranker", "50 candidates", "top 10", "Re-scores properly; fixes approximate search's mistakes"],
            ["13", "Assembler", "top 10 + PNG paths", "Claude message", "Swaps descriptions back out for real image bytes"],
            ["14", "Claude", "text + images + question", "answer", "Reasons over both modalities, cites sources"],
            ["15", "Response", "model output", "JSON", "Answer, page citations, and the image files"],
        ],
    )),
    ("callout", ("key", "The critical junction is block 13",
                 "This is where the trick from block 5 reverses. During search, an image was "
                 "represented by its description. At block 13 that description is discarded and the "
                 "actual PNG is loaded from disk and put into the message. So Claude never sees the "
                 "caption - it sees the diagram, and can read details the caption never mentioned. "
                 "Then the same file is returned to the user.")),

    ("pagebreak", None),

    ("h2", "Diagram 4 - What a stored record looks like"),
    ("diagram", ("One Qdrant record of type image", """
   ONE QDRANT RECORD  (image type)
   +-----------------------------------------------------------+
   |  id:        "F-0042"                                      |
   |                                                           |
   |  VECTORS --- dense:  [0.021, -0.334, 0.912, ... x1024]    |--> block 10a
   |          +-- sparse: {oauth:0.8, pkce:0.9, token:0.6}     |--> block 10b
   |                                                           |
   |  PAYLOAD                                                  |
   |   +- text:      "OAuth2 authorization-code flow with      |
   |   |              PKCE. Sequence diagram, four actors..."  |--> searched
   |   +- type:      "image"                                   |--> quota 11
   |   +- doc_id:    "payments-api-spec"                       |
   |   +- page:      12                                        |--> citation
   |   +- bbox:      [72, 210, 468, 540]                       |
   |   +- file_path: "artifacts/payments-api-spec/             |--> block 13
   |                  page12_img01.png"  ----------------+     |    loads this
   +----------------------------------------------------|-----+
                                                         v
                                             +--------------------+
                                             |  ACTUAL PNG BYTES  |
                                             |   (on disk)        |
                                             +--------------------+
""".strip("\n"))),
    ("p", "The record is a **pointer with a searchable label**. The label (`text`) is what search "
          "matches on. The pointer (`file_path`) is what gets dereferenced at block 13 and returned at "
          "block 15. Text chunks use the identical shape - `type: \"text\"`, no `file_path`."),

    ("h2", "Diagram 5 - Runtime components"),
    ("diagram", ("What runs where", """
   +--------------------------------------------------------------+
   |  BROWSER                                                     |
   |  +--------------------------------------------------------+  |
   |  |  STREAMLIT UI    chat box - streamed answer            |  |
   |  |                  images rendered inline                |  |
   |  +--------------------------+-----------------------------+  |
   +-----------------------------|--------------------------------+
                                 | HTTP / SSE
                                 v
   +--------------------------------------------------------------+
   |  FASTAPI                                     :8000           |
   |   POST /ingest            -> ingestion pipeline (background) |
   |   GET  /ingest/{job}      -> progress                        |
   |   POST /query             -> retrieval + generation (stream) |
   |   GET  /artifacts/{...}   -> serves the PNG files            |
   +---+-----------------+------------------+---------------------+
       |                 |                  |
       v                 v                  v
  +---------+    +--------------+    +--------------+
  | QDRANT  |    |  LOCAL DISK  |    |  EXTERNAL    |
  | :6333   |    |  artifacts/  |    |  APIs        |
  | (Docker)|    |  data/       |    |  - Anthropic |
  +---------+    +--------------+    |  - Voyage    |
                                     +--------------+
""".strip("\n"))),
    ("p", "Everything except the two API providers runs on your machine. Qdrant is one `docker run`; "
          "artifacts are plain files FastAPI serves statically; Streamlit talks to FastAPI over HTTP "
          "so the UI can be swapped later without touching the pipeline."),

    ("h2", "The two ideas the diagrams are really showing"),
    ("numbers", [
        "**Images become text at block 5, and become images again at block 13.** Search operates on "
        "descriptions; answering operates on pixels; the user receives the original file. Every "
        "design choice downstream follows from that round trip.",
        "**The pipelines are decoupled by storage.** Ingestion never calls retrieval. That means you "
        "can re-ingest a document, swap the embedding model, or rebuild the index without touching "
        "the query path - and you can tune retrieval without re-parsing 40 pages every time.",
    ]),

    ("pagebreak", None),

    # ================================================================
    # TURN 4
    # ================================================================
    ("turn", ("user", "Turn 4 - User", "Document export request")),
    ("p", "Add all conversation with response in the PDF document, and word document with colourful "
          "design and give"),

    ("turn", ("assistant", "Turn 4 - Response", "This document")),
    ("p", "The full conversation was transcribed into a structured content module and rendered to "
          "both PDF and DOCX with a shared colour design system. Environment work required: Python "
          "3.12 was located via the `py` launcher (the `python` command on PATH is the Microsoft "
          "Store stub and does not work), and `python-docx`, `reportlab` and `fonttools` were "
          "installed."),
    ("p", "Font coverage was verified before rendering, because the block diagrams use box-drawing "
          "characters that many PDF base fonts lack. Consolas covers the full box-drawing and "
          "circled-digit range; Calibri covers the prose set. The diagrams in this document use an "
          "ASCII-safe transliteration so they render identically in both formats and in any viewer."),

    ("pagebreak", None),

    # ================================================================
    # APPENDIX
    # ================================================================
    ("h1", "Appendix - Quick reference"),

    ("h2", "The 15 blocks at a glance"),
    ("table", (
        ["Phase", "#", "Block", "One-line function"],
        [
            ["Ingest", "1", "Parser", "Walk pages, expose text/images/tables/renders"],
            ["Ingest", "2", "Text blocks", "Heading detection from font size"],
            ["Ingest", "3", "Image extractor", "Extract PNGs, filter out logos and rules"],
            ["Ingest", "4", "Table detector", "Ruled tables to rows, borderless to crops"],
            ["Ingest", "5", "Enricher", "Claude vision writes searchable text for pictures"],
            ["Ingest", "6", "Chunker", "Section-aware 500-800 word units"],
            ["Ingest", "7", "Embedder", "Text to vectors (Voyage)"],
            ["Ingest", "8", "Indexer", "Dense + sparse indexes in Qdrant"],
            ["Query", "9", "Query embedder", "Question to vector, same model"],
            ["Query", "10", "Dual search", "Dense (meaning) + BM25 (exact)"],
            ["Query", "11", "Fusion + quota", "RRF merge, reserve image slots"],
            ["Query", "12", "Reranker", "Cross-encoder precision pass"],
            ["Query", "13", "Assembler", "Load real PNGs, interleave with text"],
            ["Query", "14", "Claude Opus 5", "Answer from text + images"],
            ["Query", "15", "Response", "answer + citations + image files"],
        ],
    )),

    ("h2", "Decisions locked"),
    ("kv", [
        ("Retrieval strategy", "Summary-indexed (Claude vision captions, embedded as text)"),
        ("Vector store", "Qdrant local via Docker, artifacts on disk"),
        ("Delivery surface", "FastAPI (REST + SSE) plus Streamlit UI"),
        ("Generation model", "claude-opus-5 with adaptive thinking and streaming"),
        ("Embedding model", "voyage-3-large; reranking with voyage-rerank-2"),
    ]),

    ("h2", "Immediate next actions"),
    ("numbers", [
        "Add `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY` to `.env`.",
        "Place a representative PDF (with diagrams and at least one borderless table) in `data/`.",
        "Run Phase 1 - scaffold plus parser and extractor, no LLM calls - and inspect "
        "`artifacts/{doc_id}/` plus `manifest.json` to judge extraction quality.",
        "Only after extraction looks clean, proceed to Phase 2 (enrichment and indexing).",
    ]),

    ("callout", ("tip", "The rule to remember",
                 "Extraction quality caps everything downstream. Spend the time in blocks 3 and 5 "
                 "before touching retrieval tuning - no amount of reranking rescues a badly "
                 "extracted, badly captioned index.")),
]
