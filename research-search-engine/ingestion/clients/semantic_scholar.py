'''import sys
from pathlib import Path

# Adds the project root directory to Python's search path
sys.path.append(str(Path(__file__).resolve().parent.parent.parent))

# Now your import will work anywhere
import uuid
import requests
from storage.models import Paper

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

def search_semantic_scholar(query: str, limit: int) -> list[Paper]:
    """Hits the Semantic Scholar API and returns a list of Paper objects."""
    params = {
        "query": query,
        "limit": limit,
        "fields": "paperId,externalIds,title,abstract,year,authors,venue"
    }

    try:
        response = requests.get(API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print(f"Error fetching data from Semantic Scholar: {e}")
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
            title=item.get("title", "Untitled"),
            authors=author_names,
            abstract=item.get("abstract"),
            year=item.get("year"),
            venue=item.get("venue"),
            source="semantic_scholar"
        )
        papers.append(paper)

    return papers


if __name__ == "__main__":
    user_query = input("Enter search query: ")
    user_limit = int(input("How many papers do you want to fetch? "))

    print(f"\nSearching Semantic Scholar for: '{user_query}' (limit: {user_limit})...\n")
    results = search_semantic_scholar(user_query, user_limit)

    for idx, paper in enumerate(results, start=1):
        print(f"--- Paper {idx} ---")
        print(f"Title:   {paper.title}")
        print(f"Year:    {paper.year}")
        print(f"DOI:     {paper.doi}")
        print(f"Authors: {', '.join(paper.authors[:3])}")
        print()'''
# ingestion/clients/semantic_scholar.py
import sys
from pathlib import Path

# Adds the project root directory to Python's import search path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Your normal imports follow here:
import time
import uuid
import requests
from storage.models import Paper
import time
import uuid
import requests
from storage.models import Paper

API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

def search_semantic_scholar(query: str, limit: int = 5, retries: int = 3) -> list[Paper]:
    """Hits the Semantic Scholar API with automatic retry on 429 rate limit."""
    
    params = {
        "query": query,
        "limit": limit,
        "fields": "paperId,externalIds,title,abstract,year,authors,venue,fieldsOfStudy,citationCount,url"
    }
    
    headers = {
        "User-Agent": "AcademicProjectSearchEngine/1.0 (contact@example.com)"
    }

    for attempt in range(retries):
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
        except Exception as e:
            if attempt == retries - 1:
                print(f"[Error fetching data]: {e}")
                return []
            time.sleep(2)
    else:
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
            url=item.get("url")
        )
        papers.append(paper)

    return papers


if __name__ == "__main__":
    results = search_semantic_scholar("quantum computing", limit=3)
    print(f"\nFetched {len(results)} papers:")
    for p in results:
        print(f"- {p.title} ({p.year})")
