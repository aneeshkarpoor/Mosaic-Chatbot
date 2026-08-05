from __future__ import annotations

import csv
import math
import os
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
        community_terms = (
            "event",
            "community",
            "coffee hour",
            "support group",
            "subgroup",
        )
        return any(term in content for term in community_terms)

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
    """Auditable field-weighted retriever for the curated Mosaic library.

    Supabase is the primary source when DATABASE_URL is configured. The local CSV
    remains available as a fallback so the prototype can still run during local
    development or a temporary database outage.
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
        self.database_url = os.getenv("DATABASE_URL", "").strip()
        self.source = "csv"
        self.load_warning: str | None = None
        self.resources = self._load()
        self._document_frequency = self._build_document_frequency()

    def _load(self) -> list[Resource]:
        if self.database_url:
            try:
                resources = self._load_from_supabase()
                self.source = "supabase"
                print(f"Loaded {len(resources)} Mosaic resources from Supabase.")
                return resources
            except Exception as error:
                self.load_warning = f"{type(error).__name__}: {error}"
                print(
                    "Supabase knowledge-base load failed. "
                    f"Using CSV fallback instead: {self.load_warning}"
                )

        resources = self._load_from_csv()
        print(f"Loaded {len(resources)} Mosaic resources from CSV.")
        return resources

    def _load_from_supabase(self) -> list[Resource]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError(
                "Supabase support requires psycopg. Run: pip install -r requirements.txt"
            ) from error

        query = """
            SELECT
                "Title" AS title,
                "Content Type" AS content_type,
                "Category" AS category,
                "Age Range" AS age_range,
                "Topic Tags" AS topic_tags,
                "Values/Intent Tags" AS values_tags,
                "Author/Guide" AS author,
                "Date Published" AS date_published,
                "Source URL" AS source_url,
                "Summary (RAG snippet)" AS summary,
                "Key Takeaway (for pathway output)" AS key_takeaway,
                "Parent Need / Goal" AS parent_need
            FROM public.mosaic_resources
            ORDER BY "Title";
        """

        with psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=15,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                rows = cursor.fetchall()

        resources = [
            Resource(
                id=f"R{index:03d}",
                title=self._clean(row.get("title")),
                content_type=self._clean(row.get("content_type")),
                category=self._clean(row.get("category")),
                age_range=self._clean(row.get("age_range")),
                topic_tags=self._clean(row.get("topic_tags")),
                values_tags=self._clean(row.get("values_tags")),
                author=self._clean(row.get("author")),
                date_published=self._clean(row.get("date_published")),
                source_url=self._clean(row.get("source_url")),
                summary=self._clean(row.get("summary")),
                key_takeaway=self._clean(row.get("key_takeaway")),
                parent_need=self._clean(row.get("parent_need")),
            )
            for index, row in enumerate(rows, start=1)
        ]
        if not resources:
            raise ValueError("The Supabase mosaic_resources table returned no records.")
        return resources

    def _load_from_csv(self) -> list[Resource]:
        resources: list[Resource] = []
        with self.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            for index, row in enumerate(csv.DictReader(handle), start=1):
                resources.append(
                    Resource(
                        id=f"R{index:03d}",
                        title=self._clean(row.get("Title")),
                        content_type=self._clean(row.get("Content Type")),
                        category=self._clean(row.get("Category")),
                        age_range=self._clean(row.get("Age Range")),
                        topic_tags=self._clean(row.get("Topic Tags")),
                        values_tags=self._clean(row.get("Values/Intent Tags")),
                        author=self._clean(row.get("Author/Guide")),
                        date_published=self._clean(row.get("Date Published")),
                        source_url=self._clean(row.get("Source URL")),
                        summary=self._clean(row.get("Summary (RAG snippet)")),
                        key_takeaway=self._clean(row.get("Key Takeaway (for pathway output)")),
                        parent_need=self._clean(row.get("Parent Need / Goal")),
                    )
                )
        if not resources:
            raise ValueError(f"No resources found in {self.csv_path}")
        return resources

    @staticmethod
    def _clean(value: object) -> str:
        return "" if value is None else str(value).strip()

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
