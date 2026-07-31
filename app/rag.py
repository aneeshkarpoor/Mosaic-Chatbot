from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "can", "do", "for", "from", "had", "has", "have", "how", "i", "in",
    "is", "it", "me", "my", "of", "on", "or", "our", "so", "that", "the",
    "their", "them", "they", "this", "to", "we", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
}


def tokenize(value: str) -> list[str]:
    return [token for token in TOKEN_RE.findall((value or "").lower()) if token not in STOP_WORDS]


def parse_age_range(value: str) -> tuple[int, int] | None:
    text = (value or "").lower()
    if "all ages" in text or "parent" in text:
        return None
    numbers = [int(number) for number in re.findall(r"\d+", text)]
    if not numbers:
        return None
    if len(numbers) == 1:
        return numbers[0], 99 if "+" in text else numbers[0]
    return min(numbers[0], numbers[1]), max(numbers[0], numbers[1])


@dataclass(frozen=True)
class Resource:
    id: str
    title: str
    content_type: str
    category: str
    age_range: str
    topic_tags: str
    values_tags: str
    author: str
    date_published: str
    source_url: str
    summary: str
    key_takeaway: str
    parent_need: str

    @property
    def is_community(self) -> bool:
        content = self.content_type.lower()
        return "event" in content or "community circle" in content or "coffee hour" in content

    def public_dict(self) -> dict:
        return {**asdict(self), "is_community": self.is_community}

    def context_block(self) -> str:
        return (
            f"[{self.id}] {self.title}\n"
            f"Type: {self.content_type}; Category: {self.category}; Age range: {self.age_range}\n"
            f"Topics: {self.topic_tags}\nValues/intent: {self.values_tags}\n"
            f"Summary: {self.summary}\nKey takeaway: {self.key_takeaway}\n"
            f"Parent need/goal: {self.parent_need}\nURL: {self.source_url}"
        )


class KnowledgeBase:
    """Small, auditable field-weighted retriever for the curated Mosaic CSV.

    This is intentionally lexical for the first prototype. It is deterministic,
    needs no second AI provider, and makes relevance easy to inspect with a curated
    corpus. The interface can later be backed by embeddings without changing API
    handlers or the browser application.
    """

    FIELD_WEIGHTS = {
        "title": 3.0,
        "category": 2.0,
        "topic_tags": 4.0,
        "values_tags": 3.5,
        "summary": 1.8,
        "key_takeaway": 2.0,
        "parent_need": 3.0,
    }

    def __init__(self, csv_path: Path):
        self.csv_path = Path(csv_path)
        self.resources = self._load()
        self._document_frequency = self._build_document_frequency()

    def _load(self) -> list[Resource]:
        resources: list[Resource] = []
        with self.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=1):
                resources.append(
                    Resource(
                        id=f"R{index:03d}",
                        title=row.get("Title", "").strip(),
                        content_type=row.get("Content Type", "").strip(),
                        category=row.get("Category", "").strip(),
                        age_range=row.get("Age Range", "").strip(),
                        topic_tags=row.get("Topic Tags", "").strip(),
                        values_tags=row.get("Values/Intent Tags", "").strip(),
                        author=row.get("Author/Guide", "").strip(),
                        date_published=row.get("Date Published", "").strip(),
                        source_url=row.get("Source URL", "").strip(),
                        summary=row.get("Summary (RAG snippet)", "").strip(),
                        key_takeaway=row.get("Key Takeaway (for pathway output)", "").strip(),
                        parent_need=row.get("Parent Need / Goal", "").strip(),
                    )
                )
        if not resources:
            raise ValueError(f"No resources found in {self.csv_path}")
        return resources

    def _build_document_frequency(self) -> dict[str, int]:
        frequencies: dict[str, int] = {}
        for resource in self.resources:
            terms = set()
            for field in self.FIELD_WEIGHTS:
                terms.update(tokenize(getattr(resource, field)))
            for term in terms:
                frequencies[term] = frequencies.get(term, 0) + 1
        return frequencies

    def _age_bonus(self, resource: Resource, ages: Iterable[int]) -> float:
        ages = list(ages)
        if not ages:
            return 0.0
        bounds = parse_age_range(resource.age_range)
        if bounds is None:
            return 0.5
        low, high = bounds
        matches = sum(low <= age <= high for age in ages)
        return 2.0 * matches / len(ages) if matches else -1.0

    def _score(self, resource: Resource, query_terms: list[str], ages: Iterable[int]) -> float:
        score = self._age_bonus(resource, ages)
        total_docs = len(self.resources)
        for field, field_weight in self.FIELD_WEIGHTS.items():
            field_terms = tokenize(getattr(resource, field))
            counts: dict[str, int] = {}
            for term in field_terms:
                counts[term] = counts.get(term, 0) + 1
            for term in query_terms:
                if term in counts:
                    inverse_frequency = math.log((total_docs + 1) / (self._document_frequency.get(term, 0) + 1)) + 1
                    score += field_weight * inverse_frequency * (1 + math.log(counts[term]))
        return score

    def search(
        self,
        query: str,
        *,
        ages: Iterable[int] = (),
        limit: int = 6,
        community: bool | None = None,
    ) -> list[Resource]:
        query_terms = tokenize(query)
        candidates = [
            resource
            for resource in self.resources
            if community is None or resource.is_community is community
        ]
        ranked = sorted(
            ((self._score(resource, query_terms, ages), resource) for resource in candidates),
            key=lambda pair: (-pair[0], pair[1].id),
        )
        positive = [resource for score, resource in ranked if score > 0]
        return positive[:limit] or [resource for _, resource in ranked[:limit]]

    def context(self, resources: Iterable[Resource]) -> str:
        return "\n\n".join(resource.context_block() for resource in resources)
