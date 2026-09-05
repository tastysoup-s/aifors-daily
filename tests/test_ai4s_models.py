from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.models import (
    AI4S_CATEGORY_IDS,
    AI4SAnalysis,
    AI4SSummary,
    AnalyzerResult,
    Item,
)


def _item() -> Item:
    return Item(
        url="https://arxiv.org/abs/2609.00001",
        title="AI4S paper",
        content="Abstract",
        published_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
        source="arxiv:arxiv-biology-medicine",
    )


def _analyzer_result(**overrides) -> AnalyzerResult:
    values = {
        "is_ai4s": True,
        "primary_category": "biology",
        "secondary_categories": ["medicine"],
        "content_type": "paper",
        "score": 8,
        "tags": ["protein-design"],
        "model": "test-model",
        "cost_usd": 0.001,
    }
    values.update(overrides)
    return AnalyzerResult(**values)


def _summary() -> AI4SSummary:
    return AI4SSummary(
        scientific_problem="Predict protein structure.",
        ai_method="A diffusion model.",
        main_result="Improved the reported benchmark.",
        innovation="Jointly models sequence and structure.",
        scientific_significance="Supports protein design experiments.",
        resources="https://example.com/code",
        model="test-model",
        cost_usd=0.002,
        assessment="Evidence supports the result, but broader validation is still needed.",
    )


def test_analyzer_result_valid():
    result = _analyzer_result()

    assert result.is_ai4s is True
    assert result.primary_category == "biology"
    assert result.secondary_categories == ["medicine"]
    assert result.content_type == "paper"
    assert result.score == 8


def test_analyzer_result_allows_non_ai4s_without_categories():
    result = _analyzer_result(
        is_ai4s=False,
        primary_category=None,
        secondary_categories=[],
        score=1,
    )

    assert result.primary_category is None


def test_analyzer_result_rejects_non_boolean_is_ai4s():
    with pytest.raises(ValueError, match="boolean"):
        _analyzer_result(is_ai4s="true")


def test_analyzer_result_rejects_category_for_non_ai4s_content():
    with pytest.raises(ValueError, match="primary_category=None"):
        _analyzer_result(is_ai4s=False, secondary_categories=[])


def test_analyzer_result_rejects_secondary_category_for_non_ai4s_content():
    with pytest.raises(ValueError, match="cannot have secondary_categories"):
        _analyzer_result(is_ai4s=False, primary_category=None)


def test_analyzer_result_rejects_invalid_primary_category():
    with pytest.raises(ValueError, match="valid primary_category"):
        _analyzer_result(primary_category="finance")


@pytest.mark.parametrize(
    ("secondary_categories", "message"),
    [
        (["medicine", "medicine"], "duplicates"),
        (["biology"], "primary_category"),
        (["finance"], "invalid category"),
        (["medicine", "chemistry", "materials"], "more than 2"),
    ],
)
def test_analyzer_result_rejects_invalid_secondary_categories(
    secondary_categories, message
):
    with pytest.raises(ValueError, match=message):
        _analyzer_result(secondary_categories=secondary_categories)


@pytest.mark.parametrize("score", [-1, 11, True])
def test_analyzer_result_rejects_invalid_score(score):
    with pytest.raises(ValueError, match="score"):
        _analyzer_result(score=score)


def test_analyzer_result_rejects_invalid_content_type():
    with pytest.raises(ValueError, match="content_type"):
        _analyzer_result(content_type="blog")


def test_ai4s_summary_construction():
    summary = _summary()

    assert summary.scientific_problem == "Predict protein structure."
    assert summary.ai_method == "A diffusion model."
    assert summary.main_result == "Improved the reported benchmark."
    assert summary.innovation == "Jointly models sequence and structure."
    assert summary.scientific_significance == "Supports protein design experiments."
    assert summary.resources == "https://example.com/code"
    assert summary.assessment.startswith("Evidence supports")


@pytest.mark.parametrize(
    "overrides",
    [
        {"scientific_problem": 123},
        {"assessment": 123},
        {"model": ""},
        {"cost_usd": -0.1},
        {"cost_usd": True},
        {"cost_usd": float("nan")},
    ],
)
def test_ai4s_summary_rejects_invalid_values(overrides):
    values = {
        "scientific_problem": "problem",
        "ai_method": "method",
        "main_result": "result",
        "innovation": "innovation",
        "scientific_significance": "significance",
        "resources": "none",
        "model": "test-model",
        "cost_usd": 0.001,
        "assessment": "bounded judgement",
    }
    values.update(overrides)

    with pytest.raises(ValueError, match="AI4SSummary"):
        AI4SSummary(**values)


def test_ai4s_analysis_combines_item_analyzer_and_summary():
    analysis = AI4SAnalysis(
        item=_item(), analyzer=_analyzer_result(), summary=_summary()
    )

    assert analysis.item.title == "AI4S paper"
    assert analysis.analyzer.primary_category == "biology"
    assert analysis.summary is not None
    assert analysis.total_cost_usd == pytest.approx(0.003)


def test_ai4s_analysis_allows_missing_summary():
    analysis = AI4SAnalysis(item=_item(), analyzer=_analyzer_result(), summary=None)

    assert analysis.summary is None
    assert analysis.total_cost_usd == pytest.approx(0.001)


def test_model_category_ids_match_taxonomy_config():
    taxonomy = yaml.safe_load(
        Path("config/categories.yaml").read_text(encoding="utf-8")
    )

    assert set(taxonomy["categories"]) == AI4S_CATEGORY_IDS
