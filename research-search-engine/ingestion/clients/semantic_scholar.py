import sys
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
        print()