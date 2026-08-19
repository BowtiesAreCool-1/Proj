# storage/models.py
from dataclasses import dataclass, field

@dataclass
class Paper:
    id: str                       # internal UUID, generated at ingestion time
    doi: str | None               # primary identifier when available
    arxiv_id: str | None          # fallback identifier for arXiv-only papers
    title: str
    authors: list[str]
    abstract: str | None
    year: int | None
    venue: str | None             # journal or conference name
    field_of_study: list[str] = field(default_factory=list)
    citation_count: int | None = None
    source: str = "unknown"       # which API this record came from
    url: str | None = None
    embedding: list[float] | None = None   # populated later by the AI layer