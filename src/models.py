from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


AI4SCategory = Literal[
    "biology",
    "medicine",
    "chemistry",
    "materials",
    "physics",
    "earth",
    "general",
]
AI4SContentType = Literal[
    "paper",
    "model",
    "dataset",
    "benchmark",
    "tool",
    "project",
    "research_news",
]

AI4S_CATEGORY_IDS: frozenset[str] = frozenset(
    {"biology", "medicine", "chemistry", "materials", "physics", "earth", "general"}
)
AI4S_CONTENT_TYPE_IDS: frozenset[str] = frozenset(
    {"paper", "model", "dataset", "benchmark", "tool", "project", "research_news"}
)
MAX_SECONDARY_CATEGORIES = 2


@dataclass
class Item:
    url: str
    title: str
    content: str
    published_at: datetime
    source: str
    raw: dict = field(default_factory=dict)


@dataclass
class Score:
    score: int          # 0-10
    tags: list[str]
    model: str
    cost_usd: float


@dataclass
class Summary:
    innovation: str
    approach: str
    metrics: str
    links: str
    why_relevant: str
    model: str
    cost_usd: float


@dataclass
class Analysis:
    """Joined view: an item with its score (always) and summary (if score >= threshold)."""

    url: str
    title: str
    source: str
    content: str
    published_at: datetime
    score: Score
    summary: Summary | None
    surfaced_at: datetime | None = None  # When item was first part of any digest

    @property
    def total_cost_usd(self) -> float:
        return self.score.cost_usd + (self.summary.cost_usd if self.summary else 0.0)


@dataclass
class AnalyzerResult:
    """Combined AI4S classification, relevance decision, and ranking score."""

    is_ai4s: bool
    primary_category: AI4SCategory | None
    secondary_categories: list[AI4SCategory]
    content_type: AI4SContentType
    score: int
    tags: list[str]
    model: str
    cost_usd: float

    def __post_init__(self) -> None:
        if not isinstance(self.is_ai4s, bool):
            raise ValueError("is_ai4s must be a boolean")
        if not isinstance(self.secondary_categories, list):
            raise ValueError("secondary_categories must be a list")

        if self.is_ai4s:
            if self.primary_category not in AI4S_CATEGORY_IDS:
                raise ValueError("AI4S content requires a valid primary_category")
        else:
            if self.primary_category is not None:
                raise ValueError("non-AI4S content must use primary_category=None")
            if self.secondary_categories:
                raise ValueError("non-AI4S content cannot have secondary_categories")

        if len(self.secondary_categories) > MAX_SECONDARY_CATEGORIES:
            raise ValueError(
                f"secondary_categories cannot contain more than {MAX_SECONDARY_CATEGORIES} values"
            )
        if len(set(self.secondary_categories)) != len(self.secondary_categories):
            raise ValueError("secondary_categories cannot contain duplicates")
        if any(category not in AI4S_CATEGORY_IDS for category in self.secondary_categories):
            raise ValueError("secondary_categories contains an invalid category")
        if self.primary_category in self.secondary_categories:
            raise ValueError("secondary_categories cannot contain primary_category")
        if self.content_type not in AI4S_CONTENT_TYPE_IDS:
            raise ValueError("invalid content_type")
        if isinstance(self.score, bool) or not isinstance(self.score, int):
            raise ValueError("score must be an integer")
        if not 0 <= self.score <= 10:
            raise ValueError("score must be between 0 and 10")


@dataclass
class AI4SSummary:
    scientific_problem: str
    ai_method: str
    main_result: str
    innovation: str
    scientific_significance: str
    resources: str
    model: str
    cost_usd: float


@dataclass
class AI4SAnalysis:
    """Future display/report aggregate, independent of database row layout."""

    item: Item
    analyzer: AnalyzerResult
    summary: AI4SSummary | None = None
    surfaced_at: datetime | None = None

    @property
    def total_cost_usd(self) -> float:
        return self.analyzer.cost_usd + (self.summary.cost_usd if self.summary else 0.0)
