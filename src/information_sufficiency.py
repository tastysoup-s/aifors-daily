"""Deterministic admission and ranking rules for AI4S recommendations."""

import re

from src.models import AI4SAnalysis


REQUIRED_INFORMATION_FIELDS = (
    "scientific_problem",
    "ai_method",
    "innovation",
    "scientific_significance",
)
OPTIONAL_INFORMATION_FIELDS = ("main_result",)
FACTUAL_FIELDS = REQUIRED_INFORMATION_FIELDS + OPTIONAL_INFORMATION_FIELDS
LOW_INFORMATION_MARKERS = (
    "原文未说明", "原文未披露", "原文未明确", "未说明", "未披露", "未提供",
    "信息不足", "暂无足够信息", "当前来源仅提供简短介绍",
    "仅提供项目名称和描述性标题", "条目仅提供仓库名称和描述",
    "条目仅描述为资源列表", "无实验数据", "无性能指标",
    "标题仅提及", "标题仅称", "标题仅陈述", "原文仅", "未具体说明",
)


def is_substantive(text: str | None) -> bool:
    """Reject missing-information clauses, not a factual field containing one.

    This is a placeholder heuristic, not fact verification. A separate factual
    clause (including a qualitative result) is retained regardless of length.
    """
    if not text or not text.strip():
        return False
    clauses = re.split(r"[。；;，,！!？?\n]|(?:但是|然而|但)", text)
    for part in clauses:
        clause = part.strip(" \t\r.：:‘’“”\"'")
        if clause and not clause.startswith(LOW_INFORMATION_MARKERS):
            return True
    return False


def information_score(analysis: AI4SAnalysis) -> int:
    if analysis.summary is None:
        return 0
    return sum(
        is_substantive(getattr(analysis.summary, field)) for field in FACTUAL_FIELDS
    )


def insufficient_information_reason(analysis: AI4SAnalysis) -> str | None:
    if analysis.summary is None:
        return "missing summary"
    missing = [
        field
        for field in REQUIRED_INFORMATION_FIELDS
        if not is_substantive(getattr(analysis.summary, field))
    ]
    if missing:
        return f"missing required fields: {','.join(missing)}"
    return None


def has_sufficient_information(analysis: AI4SAnalysis) -> bool:
    return insufficient_information_reason(analysis) is None


def has_substantive_assessment(analysis: AI4SAnalysis) -> bool:
    return bool(analysis.summary and is_substantive(analysis.summary.assessment))


def recommendation_quality_tier(analysis: AI4SAnalysis) -> tuple[int, int, int]:
    """Quality tier before recency and source-diversity tie breaking."""
    return (
        int(has_substantive_assessment(analysis)),
        analysis.analyzer.score,
        int(bool(analysis.summary and is_substantive(analysis.summary.main_result))),
    )


def recommendation_sort_key(analysis: AI4SAnalysis) -> tuple[int, int, int, float]:
    return (*recommendation_quality_tier(analysis), analysis.item.published_at.timestamp())
