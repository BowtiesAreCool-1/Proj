# ingestion/pipeline.py
"""
Orchestrates one search request: check the cache's freshness first, and only
fan out to the external APIs when the query is new or stale.

ASSUMPTION: this expects storage/db.py to expose the functions documented in
the architecture doc - was_recently_searched(), upsert_paper(),
record_search(), get_all_papers(). Those weren't among the files shared with
me, so double-check the signatures line up once this is wired into the real
storage layer.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio
from storage.models import Paper
from storage import db
from ingestion.clients.semantic_scholar import search_semantic_scholar
from ingestion.clients.arxiv import search_arxiv
from ingestion.normalizer import normalize_papers
from ingestion.dedupe import dedupe

# Each entry is (source_name, sync_search_function). Add crossref/openalex
# clients here once they exist - nothing else in this file needs to change,
# which is the whole point of every client sharing the same signature.
SOURCES = [
    ("semantic_scholar", search_semantic_scholar),
    ("arxiv", search_arxiv),
]


async def _fetch_from_source(name: str, search_fn, query: str, limit: int) -> list[Paper]:
    """Runs one (blocking, `requests`-based) client in a background thread so
    multiple sources can be fetched concurrently instead of one after
    another. True async (httpx.AsyncClient) would be more efficient, but
    to_thread lets the existing sync clients plug in without a rewrite."""
    try:
        return await asyncio.to_thread(search_fn, query, limit)
    except Exception as e:
        # A single flaky source shouldn't take the whole search down - log
        # and continue with whatever the other sources returned.
        print(f"[pipeline] {name} failed: {e}")
        return []


async def ingest_query(query: str, limit: int = 20, max_age_days: int = 7) -> list[Paper]:
    """
    1. If this query was searched recently, skip external calls entirely and
       let the search layer work directly off the local cache.
    2. Otherwise, fan out to all configured clients in parallel, normalize,
       de-duplicate, and upsert each result into the DB.
    3. Record the search so it counts as fresh for future requests.
    """
    if db.was_recently_searched(query, max_age_days=max_age_days):
        return db.get_all_papers()

    results = await asyncio.gather(
        *(_fetch_from_source(name, fn, query, limit) for name, fn in SOURCES)
    )

    all_papers = [paper for source_results in results for paper in source_results]
    normalized = normalize_papers(all_papers)
    deduped = dedupe(normalized)

    for paper in deduped:
        db.upsert_paper(paper)

    db.record_search(query, len(deduped))
    return deduped


if __name__ == "__main__":
    papers = asyncio.run(ingest_query("quantum computing", limit=5))
    print(f"Ingested {len(papers)} unique papers:")
    for p in papers:
        also_in = getattr(p, "also_in", None)
        tag = f" (also in: {', '.join(also_in)})" if also_in else ""
        print(f"- [{p.source}] {p.title} ({p.year}){tag}")
