# storage/db.py
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from storage.models import Paper

DEFAULT_DB_PATH = PROJECT_ROOT / "papers.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


@contextmanager
def _connect(path: str | Path = DEFAULT_DB_PATH):
    """Opens a connection and makes sure the schema exists. Re-running the
    (all IF NOT EXISTS) schema script on every connect is cheap at SQLite/MVP
    scale and means no one has to remember to call init_db() first."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(path: str | Path = DEFAULT_DB_PATH) -> None:
    """Creates tables from schema.sql if they don't already exist."""
    with _connect(path):
        pass  # _connect() already runs the schema script


def _row_key(paper: Paper) -> tuple[str, str]:
    """The identifier a paper is looked up / upserted on: DOI when present,
    otherwise arXiv ID, otherwise its own internal id. Mirrors the join-key
    logic in ingestion/dedupe.py and ingestion/normalizer.py."""
    if paper.doi:
        return ("doi", paper.doi)
    if paper.arxiv_id:
        return ("arxiv_id", paper.arxiv_id)
    return ("id", paper.id)


def _paper_to_row(paper: Paper) -> dict:
    return {
        "id": paper.id,
        "doi": paper.doi,
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "authors": json.dumps(paper.authors or []),
        "abstract": paper.abstract,
        "year": paper.year,
        "venue": paper.venue,
        "field_of_study": json.dumps(paper.field_of_study or []),
        "citation_count": paper.citation_count,
        "source": paper.source,
        "url": paper.url,
        "also_in": json.dumps(getattr(paper, "also_in", []) or []),
        "cached_at": (paper.cached_at or datetime.utcnow()).isoformat(),
        "citation_refreshed_at": paper.citation_refreshed_at.isoformat()
        if paper.citation_refreshed_at
        else None,
        "last_seen_at": paper.last_seen_at.isoformat() if paper.last_seen_at else None,
        "times_shown": paper.times_shown or 0,
    }


def _row_to_paper(row: sqlite3.Row) -> Paper:
    paper = Paper(
        id=row["id"],
        doi=row["doi"],
        arxiv_id=row["arxiv_id"],
        title=row["title"],
        authors=json.loads(row["authors"] or "[]"),
        abstract=row["abstract"],
        year=row["year"],
        venue=row["venue"],
        field_of_study=json.loads(row["field_of_study"] or "[]"),
        citation_count=row["citation_count"],
        source=row["source"],
        url=row["url"],
        cached_at=datetime.fromisoformat(row["cached_at"]) if row["cached_at"] else None,
        citation_refreshed_at=datetime.fromisoformat(row["citation_refreshed_at"])
        if row["citation_refreshed_at"]
        else None,
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]) if row["last_seen_at"] else None,
        times_shown=row["times_shown"] or 0,
    )
    paper.also_in = json.loads(row["also_in"] or "[]")
    return paper


def upsert_paper(paper: Paper, path: str | Path = DEFAULT_DB_PATH) -> None:
    """Insert or update a paper record, keyed on doi (falling back to
    arxiv_id, then internal id, if neither identifier is present)."""
    key_col, key_val = _row_key(paper)
    row = _paper_to_row(paper)

    with _connect(path) as conn:
        existing = conn.execute(
            f"SELECT id FROM papers WHERE {key_col} = ?", (key_val,)
        ).fetchone()
        if existing:
            columns = [c for c in row if c != "id"]
            set_clause = ", ".join(f"{c} = :{c}" for c in columns)
            conn.execute(
                f"UPDATE papers SET {set_clause} WHERE id = :existing_id",
                {**row, "existing_id": existing["id"]},
            )
        else:
            columns = ", ".join(row.keys())
            placeholders = ", ".join(f":{c}" for c in row.keys())
            conn.execute(
                f"INSERT INTO papers ({columns}) VALUES ({placeholders})", row
            )


def get_all_papers(filters: dict | None = None, path: str | Path = DEFAULT_DB_PATH) -> list[Paper]:
    """Fetch papers from the local cache, optionally filtered by year/field/venue."""
    query = "SELECT * FROM papers"
    clauses = []
    params: dict = {}

    if filters:
        if filters.get("year"):
            clauses.append("year = :year")
            params["year"] = filters["year"]
        if filters.get("venue"):
            clauses.append("venue = :venue")
            params["venue"] = filters["venue"]
        if filters.get("field"):
            # field_of_study is stored as a JSON array string; LIKE is a
            # pragmatic MVP match at SQLite scale - revisit if this ever
            # moves to Postgres with a real array/JSON column type.
            clauses.append("field_of_study LIKE :field")
            params["field"] = f'%"{filters["field"]}"%'

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    with _connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_paper(r) for r in rows]


def save_embedding(paper_id: str, vector: list[float], path: str | Path = DEFAULT_DB_PATH) -> None:
    """Store a computed embedding for later semantic search."""
    with _connect(path) as conn:
        conn.execute(
            "UPDATE papers SET embedding = ? WHERE id = ?",
            (json.dumps(vector), paper_id),
        )


def was_recently_searched(
    query: str, max_age_days: int = 7, path: str | Path = DEFAULT_DB_PATH
) -> bool:
    """Checks the search_queries table for this query's last_run_at. Returns
    True if it was run within max_age_days -> safe to serve from cache."""
    with _connect(path) as conn:
        row = conn.execute(
            "SELECT last_run_at FROM search_queries WHERE query_text = ?", (query,)
        ).fetchone()
    if not row or not row["last_run_at"]:
        return False
    last_run = datetime.fromisoformat(row["last_run_at"])
    return datetime.utcnow() - last_run < timedelta(days=max_age_days)


def record_search(query: str, result_count: int, path: str | Path = DEFAULT_DB_PATH) -> None:
    """Logs that this query was just run, so future identical searches can
    skip re-fetching from external APIs."""
    with _connect(path) as conn:
        conn.execute(
            """INSERT INTO search_queries (query_text, last_run_at, result_count)
               VALUES (:query_text, :last_run_at, :result_count)
               ON CONFLICT(query_text) DO UPDATE SET
                   last_run_at = excluded.last_run_at,
                   result_count = excluded.result_count""",
            {
                "query_text": query,
                "last_run_at": datetime.utcnow().isoformat(),
                "result_count": result_count,
            },
        )


def get_papers_needing_citation_refresh(
    field: str | None = None, older_than_days: int = 90, path: str | Path = DEFAULT_DB_PATH
) -> list[Paper]:
    """Returns papers whose citation_count hasn't been refreshed recently."""
    cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
    query = "SELECT * FROM papers WHERE (citation_refreshed_at IS NULL OR citation_refreshed_at < :cutoff)"
    params: dict = {"cutoff": cutoff}
    if field:
        query += " AND field_of_study LIKE :field"
        params["field"] = f'%"{field}"%'

    with _connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_paper(r) for r in rows]


def mark_papers_seen(paper_ids: list[str], path: str | Path = DEFAULT_DB_PATH) -> None:
    """Sets last_seen_at = now() and increments times_shown for each paper id
    actually displayed in a result (not every paper merely fetched)."""
    now = datetime.utcnow().isoformat()
    with _connect(path) as conn:
        conn.executemany(
            "UPDATE papers SET last_seen_at = ?, times_shown = times_shown + 1 WHERE id = ?",
            [(now, pid) for pid in paper_ids],
        )


def prune_unused_papers(older_than_days: int = 180, path: str | Path = DEFAULT_DB_PATH) -> int:
    """Deletes papers never shown to a user and past the cutoff. Returns the
    number of rows deleted."""
    cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
    with _connect(path) as conn:
        cursor = conn.execute(
            """DELETE FROM papers
               WHERE (last_seen_at IS NOT NULL AND last_seen_at < :cutoff)
                  OR (last_seen_at IS NULL AND cached_at < :cutoff)""",
            {"cutoff": cutoff},
        )
        return cursor.rowcount


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {DEFAULT_DB_PATH}")
