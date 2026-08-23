# ingestion/clients/arxiv.py
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import uuid
import requests
import xml.etree.ElementTree as ET
from storage.models import Paper

ARXIV_API_URL = "http://export.arxiv.org/api/query"

def search_arxiv(query: str, limit: int = 5) -> list[Paper]:
    """Queries the arXiv API and returns a list of Paper objects."""
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit
    }
    
    try:
        response = requests.get(ARXIV_API_URL, params=params, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"[arXiv Error]: {e}")
        return []

    # Parse the XML response
    root = ET.fromstring(response.content)
    namespace = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    
    papers = []
    for entry in root.findall("atom:entry", namespace):
        # Extract arXiv ID from the entry ID url (e.g., http://arxiv.org/abs/2101.12345v1)
        raw_id = entry.find("atom:id", namespace).text if entry.find("atom:id", namespace) is not None else ""
        arxiv_id = raw_id.split("/abs/")[-1] if "/abs/" in raw_id else None

        title = entry.find("atom:title", namespace).text.strip().replace("\n", " ") if entry.find("atom:title", namespace) is not None else "Untitled"
        abstract = entry.find("atom:summary", namespace).text.strip().replace("\n", " ") if entry.find("atom:summary", namespace) is not None else None
        
        # Published year
        published = entry.find("atom:published", namespace).text if entry.find("atom:published", namespace) is not None else ""
        year = int(published[:4]) if len(published) >= 4 and published[:4].isdigit() else None

        # Authors list
        author_elements = entry.findall("atom:author", namespace)
        authors = [a.find("atom:name", namespace).text for a in author_elements if a.find("atom:name", namespace) is not None]

        # Extract DOI if present
        doi_elem = entry.find("arxiv:doi", namespace)
        doi = doi_elem.text if doi_elem is not None else None

        # Primary category/field of study
        category_elem = entry.find("arxiv:primary_category", namespace)
        field_of_study = [category_elem.attrib.get("term")] if category_elem is not None and "term" in category_elem.attrib else []

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
            url=raw_id
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
