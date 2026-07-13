# RAG-Based Healthcare Query Assistant

A multi-agent hospital assistant. One chat box, two specialists behind it:

- **Patient-record questions** (*"How many diabetic patients are there?"*) → a **natural-language → SQL agent** that runs one read-only query over a normalized SQLite database.
- **Hospital-policy questions** (*"Is prior insurance approval required for surgery?"*) → a **retrieval-augmented (RAG) agent** that answers only from five policy documents and cites them — or refuses if nothing relevant is retrieved.

An **orchestrator** routes each question; a **formatter** writes the final reply. Every answer shows its evidence (the exact SQL, or the retrieved sources and their similarity scores), so nothing is taken on faith.

The notebook `RAG_Healthcare_Query_Assistant.ipynb` is the primary deliverable. The `webapp/` folder is a browser UI (**MedQuery AI**) wired to the *same* pipeline.

---

## Quick start

```bash
pip install -r requirements.txt
export GROQ_API_KEY="your_key_here"      # Windows PowerShell: setx GROQ_API_KEY "your_key_here"
```

**Notebook:** open `RAG_Healthcare_Query_Assistant.ipynb` and Run All.

**Web app:**
```bash
cd webapp
python app.py
# open http://localhost:5000
```
The first request builds the database and the FAISS index (a few seconds) and downloads the small embedding model once (~90 MB). Embeddings run locally; the only external call is to Groq for the LLM.

---

## The web app (MedQuery AI)

- **Query Assistant** — ask anything; each reply shows the agent pipeline (Orchestrator → SQL/RAG → Formatter) and a route tag.
- **Clinical Context panel** — three tabs: **Sources** (retrieved policy chunks with match scores, or the generated SQL + result table for database questions), **Trace** (per-stage timing), **Stats** (dataset + session metrics).
- **Dashboard / History / Knowledge Base** — dataset snapshot, this session's questions, and the five policy documents.
- **Light / dark theme** — toggle in the top bar (or Settings); the choice is remembered.

All data shown in the UI comes straight from the backend — the SQL that ran, the real retrieved chunks, and their cosine scores. Nothing is mocked.

---

## What I changed (and didn't)

- **The notebook is byte-for-byte unchanged** from the file you uploaded. I only checked it — it runs end-to-end with no errors and scores 4/4 on the built-in SQL accuracy test.
- **New files, all in `webapp/`:** `app.py` (Flask), `engine.py` (the notebook's functions packaged for import — same prompts, same 0.35 refusal threshold, same read-only SQL guard, with per-stage timing added for the Trace panel), and `templates/` + `static/` for the UI. Plus this `README.md` and `requirements.txt`.
- One small thing worth knowing (not changed, since you asked me to leave the notebook alone): in the notebook's `schema_text()` cell, the final `print(...)` sits after the `return`, so it never runs. It's harmless dead code — the schema itself is returned correctly. Say the word if you'd like it tidied.

---

## Notes

- **Model.** Default LLM is `llama-3.3-70b-versatile` on Groq (matching your notebook). To use another without editing code: `export GROQ_MODEL="openai/gpt-oss-120b"`.
- **Data.** Synthetic [Kaggle healthcare dataset](https://www.kaggle.com/datasets/prasad22/healthcare-dataset); the five policy docs were written to be consistent with it.
- **Determinism.** The LLM runs at `temperature=0`, so routing and SQL are reproducible.
