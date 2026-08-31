# ingestion/normalizer.py
"""
Per the architecture doc, this module's textbook job is:

    def normalize(raw: dict, source: str) -> Paper

but our actual clients (semantic_scholar.py, arxiv.py) already build Paper
objects directly instead of handing back raw dicts - a reasonable shortcut
for a 2-source MVP. The tradeoff is that two clients can format the same kind
of data slightly differently: extra whitespace, a DOI with a URL prefix vs.
a bare DOI, empty strings vs. None, duplicate/near-duplicate author names.

This module cleans that up *after* a Paper is built, so dedupe.py (which
matches on doi / arxiv_id / title+authors) is comparing apples to apples no
matter which API a record came from.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import re
from storage.models import Paper

_DOI_PREFIX_RE = re.compile(r"^(https?://)?(dx\.)?doi\.org/", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")


def normalize_doi(doi: str | None) -> str | None:
    """Strips URL prefixes and lowercases a DOI, so 'https://doi.org/10.1/X'
    and '10.1/x' are recognized as the same identifier."""
    if not doi:
        return None
    cleaned = _DOI_PREFIX_RE.sub("", doi.strip())
    return cleaned.lower() or None


def normalize_title(title: str | None) -> str:
    """Lowercases, strips punctuation, and collapses whitespace. This is for
    fuzzy matching in dedupe, not for display - never store this over the
    original title."""
    if not title:
        return ""
    lowered = title.lower()
    no_punct = _NON_ALNUM_RE.sub("", lowered)
    return _WHITESPACE_RE.sub(" ", no_punct).strip()


def normalize_authors(authors: list[str] | None) -> list[str]:
    """Trims whitespace and drops empty/duplicate entries while preserving
    the original order and casing of the first occurrence."""
    if not authors:
        return []
    seen = set()
    cleaned = []
    for name in authors:
        name = (name or "").strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            cleaned.append(name)
    return cleaned


def normalize_paper(paper: Paper) -> Paper:
    """Cleans up one Paper's fields in place and returns it, so every record
    follows the same conventions before it reaches dedupe/storage."""
    paper.doi = normalize_doi(paper.doi)
    paper.arxiv_id = paper.arxiv_id.strip() if paper.arxiv_id else None
    paper.title = (paper.title or "Untitled").strip()
    paper.authors = normalize_authors(paper.authors)
    paper.abstract = paper.abstract.strip() if paper.abstract else None
    paper.venue = paper.venue.strip() if paper.venue else None
    paper.field_of_study = sorted(
        {f.strip() for f in (paper.field_of_study or []) if f and f.strip()}
    )
    return paper


def normalize_papers(papers: list[Paper]) -> list[Paper]:
    """Convenience wrapper for normalizing a whole batch at once."""
    return [normalize_paper(p) for p in papers]
