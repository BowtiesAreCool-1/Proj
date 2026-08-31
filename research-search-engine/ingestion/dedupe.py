# ingestion/dedupe.py
"""
Merges Paper records that represent the same underlying work but were
returned by different sources (e.g. the same paper turning up in both
Semantic Scholar and arXiv results).

Match order, per the architecture doc:
    1. DOI match
    2. arXiv ID match
    3. normalized title + author overlap

When two records match, the one with richer metadata is kept as the primary
record, and the source(s) it was also found in get recorded on it.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storage.models import Paper
from ingestion.normalizer import normalize_title

# Fraction of the smaller author list that must overlap (matched on last
# name) before a title match is trusted as the same paper. Titles alone can
# collide ("A Survey of Deep Learning"), so this guards against merging two
# unrelated papers that just happen to share a generic title.
TITLE_AUTHOR_OVERLAP_THRESHOLD = 0.5


def _richness_score(paper: Paper) -> int:
    """A rough proxy for 'how complete is this record' - used to decide which
    of two matching records becomes the primary one."""
    score = 0
    if paper.abstract:
        score += 2
    if paper.doi:
        score += 1
    if paper.venue:
        score += 1
    if paper.year:
        score += 1
    if paper.citation_count is not None:
        score += 1
    score += len(paper.authors or [])
    return score


def _author_overlap(a: list[str], b: list[str]) -> float:
    """Fraction of the smaller author list that also appears in the other,
    matched on last name to tolerate 'J. Smith' vs. 'John Smith'."""
    if not a or not b:
        return 0.0
    last_names_a = {n.strip().split()[-1].lower() for n in a if n.strip()}
    last_names_b = {n.strip().split()[-1].lower() for n in b if n.strip()}
    if not last_names_a or not last_names_b:
        return 0.0
    overlap = last_names_a & last_names_b
    return len(overlap) / min(len(last_names_a), len(last_names_b))


def _is_same_paper(a: Paper, b: Paper) -> bool:
    if a.doi and b.doi and a.doi == b.doi:
        return True
    if a.arxiv_id and b.arxiv_id and a.arxiv_id == b.arxiv_id:
        return True
    title_a, title_b = normalize_title(a.title), normalize_title(b.title)
    if title_a and title_a == title_b:
        return _author_overlap(a.authors, b.authors) >= TITLE_AUTHOR_OVERLAP_THRESHOLD
    return False


def _merge(primary: Paper, duplicate: Paper) -> Paper:
    """Folds `duplicate` into `primary`, filling in gaps and recording that
    the paper was also seen in duplicate's source."""
    if not primary.doi and duplicate.doi:
        primary.doi = duplicate.doi
    if not primary.arxiv_id and duplicate.arxiv_id:
        primary.arxiv_id = duplicate.arxiv_id
    if not primary.abstract and duplicate.abstract:
        primary.abstract = duplicate.abstract
    if not primary.venue and duplicate.venue:
        primary.venue = duplicate.venue
    if not primary.year and duplicate.year:
        primary.year = duplicate.year
    if primary.citation_count is None and duplicate.citation_count is not None:
        primary.citation_count = duplicate.citation_count
    if len(duplicate.authors or []) > len(primary.authors or []):
        primary.authors = duplicate.authors
    primary.field_of_study = sorted(
        set(primary.field_of_study or []) | set(duplicate.field_of_study or [])
    )

    # NOTE: `also_in` isn't a declared field on Paper yet - Python still lets
    # us set it dynamically, but add
    #     also_in: list[str] = field(default_factory=list)
    # to storage/models.py so it's a proper typed column instead of a bolt-on
    # attribute (see the write-up for why this matters for storage/db.py).
    also_in = set(getattr(primary, "also_in", []) or [])
    also_in.add(duplicate.source)
    primary.also_in = sorted(also_in)
    return primary


def dedupe(papers: list[Paper]) -> list[Paper]:
    """Merges papers that represent the same work, per the match order above."""
    merged: list[Paper] = []
    for paper in papers:
        match_index = None
        for i, existing in enumerate(merged):
            if _is_same_paper(paper, existing):
                match_index = i
                break

        if match_index is None:
            merged.append(paper)
            continue

        existing = merged[match_index]
        if _richness_score(paper) > _richness_score(existing):
            merged[match_index] = _merge(paper, existing)
        else:
            merged[match_index] = _merge(existing, paper)

    return merged
