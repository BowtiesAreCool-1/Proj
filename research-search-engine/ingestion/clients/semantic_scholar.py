# ingestion/clients/semantic_scholar.py
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
import uuid
import requests
from storage.models import Paper

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

# From the approval email. TODO: move this into an env var / config.py before
# this ever goes into a shared repo - it shouldn't sit in source control.
S2_API_KEY = "s2k-lgxuDFo3j8j5yoB2YSrUp3ElQaYVSSXlZsV7PULz"

# Semantic Scholar's limit is 1 request/sec, CUMULATIVE across all endpoints.
# Tracking the last call time at module level (not per-call) means every
# caller - retries, dedupe's citation refresh, pipeline's fan-out - shares the
# same throttle instead of each one independently thinking it's the only
# request being made.
_MIN_INTERVAL_SECONDS = 1.1  # small headroom under the 1 req/sec ceiling
_last_call_at = 0.0


def _throttle() -> None:
    """Sleeps just long enough to stay under Semantic Scholar's rate limit."""
    global _last_call_at
    elapsed = time.monotonic() - _last_call_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    _last_call_at = time.monotonic()


def search_semantic_scholar(query: str, limit: int = 5, retries: int = 3) -> list[Paper]:
    """Hits the Semantic Scholar API with automatic retry on 429 rate limit."""

    params = {
        "query": query,
        "limit": limit,
        "fields": "paperId,externalIds,title,abstract,year,authors,venue,fieldsOfStudy,citationCount,url",
    }

    headers = {
        "User-Agent": "AcademicProjectSearchEngine/1.0 (contact@example.com)",
        "x-api-key": S2_API_KEY,
    }

    data = None
    for attempt in range(retries):
        _throttle()
        try:
            response = requests.get(API_URL, params=params, headers=headers, timeout=15)

            if response.status_code == 429:
                wait_seconds = 3 * (attempt + 1)
                print(f"[Rate Limited - 429] Waiting {wait_seconds}s before retrying (attempt {attempt + 1}/{retries})...")
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            data = response.json()
            break
        except (requests.exceptions.RequestException, ValueError) as e:
            # ValueError also catches response.json() failing on a bad body
            if attempt == retries - 1:
                print(f"[Error fetching data]: {e}")
                return []
            time.sleep(2)

    if data is None:
        return []

    papers = []
    for item in data.get("data", []):
        external_ids = item.get("externalIds") or {}
        doi = external_ids.get("DOI")
        arxiv_id = external_ids.get("ArXiv")

        raw_authors = item.get("authors") or []
        author_names = [a.get("name", "") for a in raw_authors if a.get("name")]

        paper = Paper(
            id=str(uuid.uuid4()),
            doi=doi,
            arxiv_id=arxiv_id,
            title=item.get("title") or "Untitled",
            authors=author_names,
            abstract=item.get("abstract"),
            year=item.get("year"),
            venue=item.get("venue"),
            field_of_study=item.get("fieldsOfStudy") or [],
            citation_count=item.get("citationCount"),
            source="semantic_scholar",
            url=item.get("url"),
        )
        papers.append(paper)

    return papers


if __name__ == "__main__":
    results = search_semantic_scholar("quantum computing", limit=3)
    print(f"\nFetched {len(results)} papers:")
    for p in results:
        print(f"- {p.title} ({p.year})")
