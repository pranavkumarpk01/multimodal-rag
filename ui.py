r"""
Streamlit chat UI.

Also deliberately thin: it talks to the API over HTTP and renders what comes
back. No retrieval or model logic here, so the pipeline can be changed without
touching the UI and vice versa.

    1.  .\.venv\Scripts\uvicorn.exe api:app --reload
    2.  .\.venv\Scripts\streamlit.exe run ui.py
"""

import requests
import streamlit as st

from app import config

API = "http://localhost:8000"

st.set_page_config(page_title=config.PROJECT_NAME, page_icon="::", layout="wide")


def api_get(path):
    return requests.get(f"{API}{path}", timeout=30).json()


# ----------------------------------------------------------------------
# sidebar: system state
# ----------------------------------------------------------------------
with st.sidebar:
    st.title(config.PROJECT_NAME)

    try:
        health = api_get("/health")
        points = health["qdrant"]["points"]
        st.success(f"API up - {points} chunks indexed")

        with st.expander("Model chain", expanded=False):
            for i, model in enumerate(health["llm_chain"], 1):
                st.caption(f"{i}. {model}")
            st.caption(f"embeddings: {health['embed_model']}")

        docs = api_get(f"{config.API_V1_STR}/documents")["documents"]
        with st.expander(f"Documents ({len(docs)})", expanded=True):
            for doc in docs:
                st.caption(
                    f"**{doc['doc_id']}** - {doc['pages']} pages, "
                    f"{doc['chunks']} chunks, {doc['images']} images"
                )
    except Exception as err:
        st.error(f"API not reachable at {API}")
        st.caption(str(err))
        st.stop()

    st.divider()
    top_text = st.slider("Text chunks", 3, 15, config.TOP_TEXT)
    top_images = st.slider("Images", 0, 6, config.TOP_IMAGES)
    attach_images = st.toggle(
        "Send images to the model", value=True,
        help="Off = the model only reads the written descriptions. "
             "This is what the text-only fallback sees.",
    )

    st.divider()
    uploaded = st.file_uploader("Add a PDF", type="pdf")
    if uploaded and st.button("Ingest", use_container_width=True):
        response = requests.post(
            f"{API}{config.API_V1_STR}/ingest",
            files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")},
            timeout=60,
        ).json()
        st.session_state["job"] = response.get("job_id")
        st.info(f"Job {response.get('job_id')} queued - this takes a few minutes")

    if st.session_state.get("job"):
        job = api_get(f"{config.API_V1_STR}/jobs/{st.session_state['job']}")
        label = f"job {job['job_id']}: **{job['status']}**"
        if job.get("progress"):
            label += f" - page {job['progress']}"
        st.caption(label)
        if job.get("pages_done") and job.get("pages_total"):
            st.progress(min(job["pages_done"] / job["pages_total"], 1.0))
        if job["status"] == "done":
            st.success(f"{job['doc_id']}: {job['chunks']} chunks")
            st.session_state.pop("job")
        elif job["status"] == "failed":
            st.error(job.get("error", "failed"))
            st.session_state.pop("job")
        else:
            st.button("Refresh status", use_container_width=True)


# ----------------------------------------------------------------------
# main: chat
# ----------------------------------------------------------------------
st.session_state.setdefault("history", [])

for turn in st.session_state["history"]:
    with st.chat_message(turn["role"]):
        st.markdown(turn["content"])

question = st.chat_input("Ask about your documents...")

if question:
    st.session_state["history"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("Searching and answering..."):
            try:
                result = requests.post(
                    f"{API}{config.API_V1_STR}/query",
                    json={
                        "question": question,
                        "top_text": top_text,
                        "top_images": top_images,
                        "attach_images": attach_images,
                    },
                    timeout=300,
                ).json()
            except Exception as err:
                st.error(f"Request failed: {err}")
                st.stop()

        if "detail" in result:
            st.error(result["detail"])
            st.stop()

        st.markdown(result["answer"])

        saw = "saw the images" if result["saw_images"] else "read descriptions only"
        st.caption(
            f"answered by `{result['model']}` ({saw}) - "
            f"pages {result['used_pages'] or 'n/a'}"
        )

        # ---- a diagram the model drew, if one was asked for ----
        drawing = result.get("diagram")
        if drawing:
            st.markdown("---")
            st.markdown(f"**{drawing['title']}**")
            st.caption(
                ":warning: GENERATED - drawn by "
                f"`{drawing['model']}` from pages {drawing['pages_used']}. "
                "This is not a figure from the document."
            )
            try:
                st.graphviz_chart(drawing["source"], use_container_width=True)
            except Exception as err:
                st.error(f"Could not render the diagram: {err}")
            if drawing.get("explanation"):
                st.caption(drawing["explanation"])
            st.download_button(
                "Download .dot source",
                data=drawing["source"],
                file_name=f"{drawing['title'][:40].replace(' ', '_')}.dot",
                mime="text/vnd.graphviz",
            )

        # ---- the point of the whole project: the exact retrieved images ----
        images = result.get("images", [])
        if images:
            st.markdown("---")
            cited = [i for i in images if i["cited_by_model"]]
            other = [i for i in images if not i["cited_by_model"]]

            if cited:
                st.markdown("**Figures used in the answer**")
                columns = st.columns(min(len(cited), 3))
                for column, image in zip(columns, cited):
                    with column:
                        st.image(API + image["url"], use_container_width=True)
                        st.caption(
                            f"**{image['label']}** - page {image['page']}  \n"
                            f"{image['caption'][:70]}"
                        )

            if other:
                with st.expander(f"Also retrieved ({len(other)}) - not cited"):
                    columns = st.columns(min(len(other), 3))
                    for column, image in zip(columns, other):
                        with column:
                            st.image(API + image["url"], use_container_width=True)
                            st.caption(f"page {image['page']} - {image['caption'][:60]}")

        with st.expander("Retrieval detail"):
            st.caption("Which search found each source: dense = meaning, bm25 = exact words")
            for source in result.get("text_sources", []):
                st.caption(
                    f"p{source['page']} - {source['heading'][:60] or '(no heading)'} "
                    f"`{'+'.join(source['found_by'])}` rrf={source['score']}"
                )

    st.session_state["history"].append(
        {"role": "assistant", "content": result["answer"]}
    )
