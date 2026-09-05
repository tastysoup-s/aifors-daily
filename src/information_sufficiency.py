"""Deterministic factual-field admission rule for new Daily reports only."""

import re

from src.models import AI4SAnalysis


FACTUAL_FIELDS = ("scientific_problem", "ai_method", "main_result", "innovation")
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
    if information_score(analysis) < 2:
        return "insufficient factual fields"
    if not (
        is_substantive(analysis.summary.ai_method)
        or is_substantive(analysis.summary.main_result)
    ):
        return "no method or result"
    return None


def has_sufficient_information(analysis: AI4SAnalysis) -> bool:
    return insufficient_information_reason(analysis) is None
