# Code Architecture Outline
### AI-Powered, Multi-Source Research Paper Search Engine

This is a working reference for how the codebase is organized: the libraries per layer, the folder structure, the main modules and functions, the core data model, and a walkthrough of what actually happens when a user runs a search. Treat this as a living document — update it as design decisions firm up.

---

## 1. Project structure

```
research-search-engine/
├── ingestion/
│   ├── clients/
│   │   ├── semantic_scholar.py
│   │   ├── crossref.py
│   │   ├── arxiv.py
│   │   └── openalex.py
│   ├── normalizer.py
│   ├── dedupe.py
│   └── pipeline.py
│
├── storage/
│   ├── models.py
│   ├── db.py
│   └── schema.sql
│
├── search/
│   ├── keyword_index.py
│   ├── ranking.py
│   └── filters.py
│
├── ai/
│   ├── embeddings.py
│   ├── vector_store.py
│   ├── semantic_search.py
│   ├── query_understanding.py
│   ├── summarizer.py
│   └── qa_chat.py
│
├── api/                  # if web app (FastAPI)
│   ├── main.py
│   └── routes.py
│   # or, if CLI:
├── cli/
│   └── main.py
│
├── tests/
│   ├── test_ingestion.py
│   ├── test_search.py
│   └── test_ai.py
│
├── config.py
├── requirements.txt
└── README.md
```

Each top-level folder is one layer from the architecture diagram above. A person can work almost entirely inside one folder without needing to understand the internals of the others — they just need to know the function signatures other layers expose.

---

## 2. Libraries by layer

| Layer | Library | Purpose |
|---|---|---|
| Ingestion | `httpx` or `requests` | HTTP calls to external APIs |
| Ingestion | `asyncio` + `httpx.AsyncClient` | fetch multiple sources in parallel instead of one after another |
| Ingestion | `pydantic` | validate and coerce messy API responses into a clean schema |
| Ingestion | `tenacity` | retry logic for flaky API calls (optional but recommended) |
| Storage | `sqlite3` (built-in) or `SQLAlchemy` | database access; SQLAlchemy gives an ORM if you'd rather work with Python objects than raw SQL |
| Search | `rank_bm25` | BM25 ranking with almost no setup |
| Search | `re` / basic string ops | keyword matching, tokenization (or `nltk` if you want stemming/stopword removal) |
| AI | `sentence-transformers` | local, free embedding model (e.g. `all-MiniLM-L6-v2`) — no API key, runs on CPU |
| AI | `faiss-cpu` or `chromadb` | vector similarity search over embeddings |
| AI | `anthropic` or `openai` (Python SDK) | LLM calls for query understanding, summarization, Q&A |
| Interface (web) | `fastapi` + `uvicorn` | REST API and dev server |
| Interface (CLI) | `typer` or `argparse` + `rich` | command-line interface with nice output formatting |
| Testing | `pytest` | unit tests across all layers |

Everything here is pure Python — no C++ needed anywhere in this stack, consistent with the decision in the proposal.

---

## 3. Data model

The single most important design decision: define one canonical `Paper` schema early, and make every ingestion client (Semantic Scholar, CrossRef, arXiv, OpenAlex...) translate its own response format into this shape. Nothing downstream — search, ranking, AI, interface — should ever need to know which source a paper came from.

```python
# storage/models.py
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Paper:
    id: str                       # internal UUID, generated at ingestion time
    doi: str | None                # primary identifier when available
    arxiv_id: str | None           # fallback identifier for arXiv-only papers
    title: str
    authors: list[str]
    abstract: str | None
    year: int | None
    venue: str | None              # journal or conference name
    field_of_study: list[str] = field(default_factory=list)
    citation_count: int | None = None
    source: str = "unknown"        # which API this record came from
    url: str | None = None
    embedding: list[float] | None = None   # populated later by the AI layer
    cached_at: datetime | None = None       # when this record first entered our DB
    citation_refreshed_at: datetime | None = None  # last time citation_count was updated
    last_seen_at: datetime | None = None    # last time this paper was shown to a user
    times_shown: int = 0                     # how many searches actually displayed this paper
```

`doi` (falling back to `arxiv_id` when there is no DOI) is the join key used for de-duplication across sources — this is what lets results from three different APIs merge into one clean list instead of showing the same paper three times.

**`papers.db` is persistent, not temporary.** Papers are cached indefinitely once fetched — nothing gets wiped after a search. The database only grows from what people actually search for (a lazy/demand-driven cache), which keeps it small in practice: even 100,000 cached papers is well under half a gigabyte. Section 4.5 below covers how freshness is handled without needing to re-fetch everything on every request.

A second small table tracks searches themselves, separate from the papers they returned:

```sql
-- storage/schema.sql (excerpt)
CREATE TABLE search_queries (
    query_text TEXT PRIMARY KEY,
    last_run_at TIMESTAMP,
    result_count INTEGER
);
```

This is what powers the freshness check — it lets the system answer "have we already searched this recently?" without having to inspect every paper in the database.

---

## 4. Layer-by-layer function breakdown

### Ingestion layer

```python
# ingestion/clients/semantic_scholar.py
async def search(query: str, limit: int = 20) -> list[dict]:
    """Hits the Semantic Scholar API, returns raw JSON results."""

async def fetch_by_doi(doi: str) -> dict | None:
    """Look up one paper by DOI."""
```

Each client (`crossref.py`, `arxiv.py`, `openalex.py`) exposes the same two functions with the same signatures — this consistency is what makes the pipeline able to treat all sources interchangeably.

```python
# ingestion/normalizer.py
def normalize(raw: dict, source: str) -> Paper:
    """Converts one API's raw response into a canonical Paper object."""
```

```python
# ingestion/dedupe.py
def dedupe(papers: list[Paper]) -> list[Paper]:
    """Merges papers that represent the same work.
    Match order: DOI match -> arXiv ID match -> normalized title + author overlap.
    When two records match, keep the one with richer metadata (e.g. has an abstract)
    and record the extra source(s) it was also found in.
    """
```

```python
# ingestion/pipeline.py
async def ingest_query(query: str) -> list[Paper]:
    """Checks the local cache's freshness first (see 4.5); only fans out to
    external clients in parallel via asyncio.gather when the query is new or
    stale, then normalizes, de-duplicates, and writes results to the database.
    """
```

### Storage layer

```python
# storage/db.py
def init_db(path: str) -> None:
    """Creates tables from schema.sql if they don't exist."""

def upsert_paper(paper: Paper) -> None:
    """Insert or update a paper record, keyed on doi (or arxiv_id if doi is null)."""

def get_all_papers(filters: dict | None = None) -> list[Paper]:
    """Fetch papers from the local cache, optionally filtered by year/field/venue."""

def save_embedding(paper_id: str, vector: list[float]) -> None:
    """Store a computed embedding for later semantic search."""

def was_recently_searched(query: str, max_age_days: int = 7) -> bool:
    """Checks the search_queries table for this query's last_run_at.
    Returns True if it was run within max_age_days -> safe to serve from cache
    without hitting the external APIs again.
    """

def record_search(query: str, result_count: int) -> None:
    """Logs that this query was just run, so future identical/similar searches
    can skip re-fetching from external APIs.
    """

def get_papers_needing_citation_refresh(field: str = None, older_than_days: int = 90) -> list[Paper]:
    """Returns papers whose citation_count hasn't been refreshed recently.
    Citation counts are the one field that meaningfully changes after publication —
    everything else (title, authors, abstract) is effectively permanent.
    """

def mark_papers_seen(paper_ids: list[str]) -> None:
    """Called once after every search, with the IDs of papers actually shown to the
    user (i.e. after ranking/filtering, not every paper that was merely fetched).
    For each ID: sets last_seen_at = now() and increments times_shown by 1.
    This is the signal used for pruning below — a paper that's never shown in any
    search result is a paper nobody's queries actually needed.
    """

def prune_unused_papers(older_than_days: int = 180) -> int:
    """Deletes papers where last_seen_at is older than the cutoff, OR last_seen_at
    is still null and cached_at is older than the cutoff (papers that were fetched
    and stored but never actually ranked into a result a user saw). Returns the
    number of rows deleted. Run this occasionally (e.g. a manual script run monthly),
    not on every request — this is cheap insurance against unbounded growth over a
    year of development, not something that matters at this project's actual scale
    (see Section 3).
    """
```

#### 4.5 Caching & freshness strategy

The design decision: **cache what's searched, keep it indefinitely, treat it as valid unless proven otherwise.** No temporary storage, no wipe-and-refetch on every request. Concretely, `ingestion/pipeline.py` uses the freshness check like this:

```python
# ingestion/pipeline.py
async def ingest_query(query: str) -> list[Paper]:
    """1. Check was_recently_searched(query). If True, skip external calls entirely
          and let the search layer work directly off the local cache.
       2. If False (new or stale query), fan out to all configured clients in
          parallel via asyncio.gather, normalize, de-duplicate, and upsert_paper()
          each result into the DB.
       3. Call record_search(query, result_count) so this query counts as fresh
          for future requests.
    """
```

Why this works, and why the earlier "fetch → temp store → wipe" idea was worth avoiding:

- Titles, authors, and abstracts don't change after publication — there's nothing to "keep fresh" about them, so persisting them forever is safe, not risky.
- Citation counts are the one field that does drift over time. Rather than refreshing everything, only that field gets periodically updated for papers people actually care about, via `get_papers_needing_citation_refresh()` — a small, optional background job, not something that runs on every search.
- New papers still enter the system automatically: any query that isn't well-covered locally (or hasn't been run in a while) triggers a live fetch, same as before — nothing about "always serve fresh new content" is lost.
- Embeddings computed for the AI layer stay valid across searches instead of being recomputed on every request, which is what keeps semantic search fast.

**Tracking usage, and pruning what's never used.** After the search & ranking layer decides the final list of papers to actually display for a query, the interface layer calls `mark_papers_seen()` with those paper IDs (not the raw, unranked fetch results — only what a user actually saw). This is what makes `times_shown` meaningful as a "how popular is this paper in our own search history" signal, and what makes `prune_unused_papers()` safe to run: a paper only gets deleted if it's genuinely never surfaced in any result a user looked at, not just because it happened to get fetched once by a broad query.

### Search & ranking layer

```python
# search/keyword_index.py
def build_index(papers: list[Paper]) -> object:
    """Builds an in-memory keyword index (title + abstract + authors) for fast lookup."""

def search_keyword(index, query: str) -> list[Paper]:
    """Returns papers whose title/abstract/authors match the query terms."""
```

```python
# search/filters.py
def apply_filters(papers: list[Paper], year: int = None, field: str = None,
                   venue: str = None) -> list[Paper]:
    """Narrows a result set by structured filters — the 'niche down' feature."""
```

```python
# search/ranking.py
def rank_bm25(papers: list[Paper], query: str) -> list[Paper]:
    """Orders papers by BM25 relevance score against the query."""
```

### AI layer

```python
# ai/embeddings.py
def embed_text(text: str) -> list[float]:
    """Runs the local sentence-transformer model on a title+abstract; used both at
    ingestion time (to pre-compute every paper's embedding) and at query time
    (to embed the user's search query for comparison).
    """
```

```python
# ai/vector_store.py
def add(paper_id: str, vector: list[float]) -> None:
    """Adds an embedding to the FAISS/Chroma index."""

def query(vector: list[float], top_k: int = 20) -> list[str]:
    """Returns the paper_ids of the top_k most similar embeddings."""
```

```python
# ai/semantic_search.py
def semantic_search(query: str, top_k: int = 20) -> list[Paper]:
    """Embeds the query, searches the vector store, and returns matching Paper objects —
    this is what lets a search match on meaning, not just keyword overlap.
    """
```

```python
# ai/query_understanding.py
def parse_natural_language_query(user_input: str) -> dict:
    """Sends the user's plain-English request to an LLM and gets back structured output:
    {"keywords": [...], "year_from": 2023, "field": "robotics", ...}
    This is what lets someone type a full sentence instead of learning query syntax.
    """
```

```python
# ai/summarizer.py
def summarize_abstract(paper: Paper) -> str:
    """Produces a short, plain-language summary of one paper's abstract.
    Prefer extractive summarization (lightly rewording actual sentences) over
    fully generative summarization, to reduce hallucination risk.
    """
```

```python
# ai/qa_chat.py
def answer_question(question: str, paper: Paper) -> dict:
    """Retrieval-augmented answer: grounds the LLM's response in the specific paper's
    abstract/text and returns {"answer": ..., "source": paper.id} so the answer is
    always traceable back to the source paper. (Stretch feature.)
    """
```

### Interface layer

```python
# api/routes.py  (if building a web app with FastAPI)
@app.get("/search")
def search_endpoint(q: str, year: int = None, field: str = None, mode: str = "keyword"):
    """mode='keyword' -> search_keyword() + apply_filters() + rank_bm25()
    mode='semantic'  -> parse_natural_language_query() + semantic_search()
    Both paths return a merged, ranked list of Paper objects as JSON.
    """
```

---

## 5. Request lifecycle — what happens on one search

1. User types a query into the interface (CLI or web).
2. **If it's a natural-language query:** the AI layer's `parse_natural_language_query()` turns it into structured filters + keywords.
3. The system checks the local database first (`storage.get_all_papers()` with filters applied) and whether this query was recently run (`storage.was_recently_searched()`). If it's a new or stale query, the ingestion pipeline (`ingestion.pipeline.ingest_query()`) fans out to all configured APIs in parallel, normalizes and de-duplicates the results, and `upsert`s them into storage — this is the point where new papers enter the system. If the query was searched recently, this step is skipped entirely and the search runs directly against the existing cache.
4. The search layer runs keyword matching and BM25 ranking (`search.keyword_index`, `search.ranking`) on the (now larger) local dataset.
5. In parallel, the AI layer's `semantic_search()` runs the same query against the vector index for meaning-based matches.
6. The two result sets are merged (a paper found by both methods should rank higher than one found by only one), and `storage.mark_papers_seen()` is called with the IDs of the final displayed list — this is what keeps `last_seen_at`/`times_shown` accurate for future pruning.
7. The interface displays results. If the user clicks into one paper, `summarizer.summarize_abstract()` can be called on demand for a quick digest, and (stretch) `qa_chat.answer_question()` lets them ask a specific question about it.

This lifecycle is also exactly what should guide your test suite: each numbered step above corresponds to one function that can be unit-tested independently of the others, since every layer only depends on the shape of the `Paper` object, not on how another layer is implemented internally.

---

## 6. Suggested build order (maps to the proposal's timeline)

1. `storage/models.py` + `storage/db.py` — get the schema right first, everything else depends on it.
2. One ingestion client (e.g. Semantic Scholar) end-to-end, writing into storage.
3. Add the remaining ingestion clients + `dedupe.py` once the pattern is proven.
4. `search/keyword_index.py` + `search/filters.py` — basic working search.
5. `search/ranking.py` (BM25) — better result ordering.
6. `ai/embeddings.py` + `ai/vector_store.py` + `ai/semantic_search.py` — semantic search.
7. `ai/query_understanding.py` + `ai/summarizer.py` — natural language + summaries.
8. `ai/qa_chat.py` — stretch, only if time allows.
