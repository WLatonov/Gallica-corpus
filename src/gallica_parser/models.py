from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Issue:
    ark: str
    publication_date: date
    label: str


@dataclass(frozen=True, slots=True)
class SearchHit:
    issue_ark: str
    query: str
    page_number: int
    context: str

