# ingestion/clients/arxiv.py
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
import uuid
import requests
import xml.etree.ElementTree as ET
from storage.models import Paper

ARXIV_API_URL = "https://export.arxiv.org/api/query"

# arXiv's usage guidelines ask API users to keep requests to roughly one
# every 3 seconds, so this client self-throttles the same way the Semantic
# Scholar client does.
_MIN_INTERVAL_SECONDS = 3.0
_last_call_at = 0.0


def _throttle() -> None:
    global _last_call_at
    elapsed = time.monotonic() - _last_call_at
    if elapsed < _MIN_INTERVAL_SECONDS:
        time.sleep(_MIN_INTERVAL_SECONDS - elapsed)
    _last_call_at = time.monotonic()


def _text(entry, tag: str, namespace: dict) -> str | None:
    node = entry.find(tag, namespace)
    return node.text if node is not None and node.text is not None else None


def search_arxiv(query: str, limit: int = 5) -> list[Paper]:
    """Queries the arXiv API and returns a list of Paper objects."""
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
    }

    _throttle()
    try:
        response = requests.get(ARXIV_API_URL, params=params, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"[arXiv Error]: {e}")
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        print(f"[arXiv Error] Could not parse response XML: {e}")
        return []

    namespace = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    papers = []

    for entry in root.findall("atom:entry", namespace):
        raw_id = _text(entry, "atom:id", namespace) or ""
        arxiv_id = raw_id.split("/abs/")[-1] if "/abs/" in raw_id else None

        title_raw = _text(entry, "atom:title", namespace)
        title = title_raw.strip().replace("\n", " ") if title_raw else "Untitled"

        abstract_raw = _text(entry, "atom:summary", namespace)
        abstract = abstract_raw.strip().replace("\n", " ") if abstract_raw else None

        published = _text(entry, "atom:published", namespace) or ""
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None

        author_elements = entry.findall("atom:author", namespace)
        authors = []
        for a in author_elements:
            name_node = a.find("atom:name", namespace)
            if name_node is not None and name_node.text:
                authors.append(name_node.text)

        doi_elem = entry.find("arxiv:doi", namespace)
        doi = doi_elem.text if doi_elem is not None else None

        category_elem = entry.find("arxiv:primary_category", namespace)
        field_of_study = (
            [category_elem.attrib["term"]]
            if category_elem is not None and "term" in category_elem.attrib
            else []
        )

        paper = Paper(
            id=str(uuid.uuid4()),
            doi=doi,
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            abstract=abstract,
            year=year,
            venue="arXiv",
            field_of_study=field_of_study,
            source="arxiv",
            url=raw_id or None,
        )
        papers.append(paper)

    return papers


if __name__ == "__main__":
    results = search_arxiv("quantum computing", limit=3)
    print(f"Fetched {len(results)} papers from arXiv:\n")
    for idx, p in enumerate(results, start=1):
        print(f"[{idx}] {p.title}")
        print(f"    Year:     {p.year}")
        print(f"    arXiv ID: {p.arxiv_id}")
        print(f"    Authors:  {', '.join(p.authors[:2])}")
        print()
