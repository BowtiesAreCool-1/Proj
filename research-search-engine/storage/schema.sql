-- storage/schema.sql
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    doi TEXT,
    arxiv_id TEXT,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',        -- JSON array of strings
    abstract TEXT,
    year INTEGER,
    venue TEXT,
    field_of_study TEXT NOT NULL DEFAULT '[]', -- JSON array of strings
    citation_count INTEGER,
    source TEXT NOT NULL DEFAULT 'unknown',
    url TEXT,
    also_in TEXT NOT NULL DEFAULT '[]',        -- JSON array of other sources this paper was also found in (see ingestion/dedupe.py)
    embedding TEXT,                             -- JSON array of floats, populated later by the AI layer
    cached_at TEXT,
    citation_refreshed_at TEXT,
    last_seen_at TEXT,
    times_shown INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id);

CREATE TABLE IF NOT EXISTS search_queries (
    query_text TEXT PRIMARY KEY,
    last_run_at TEXT,
    result_count INTEGER
);
