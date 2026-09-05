from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ai4s_summary_enrichment import run_ai4s_summary_enrichment
from src.config import Config, Models
from src.content_enrichment import EnrichedContent
from src.information_sufficiency import has_sufficient_information
from src.models import AI4SSummary, AnalyzerResult, Item
from src.storage import Storage


def _config() -> Config:
    return Config(
        sources=[],
        keywords=[],
        models=Models(
            scorer="deepseek/deepseek-chat",
            summarizer="deepseek/deepseek-chat",
        ),
        score_threshold=7,
    )


def _summary(**changes) -> AI4SSummary:
    value = AI4SSummary(
        scientific_problem="研究明确的科学问题。",
        ai_method="采用机器学习方法。",
        main_result="原文未披露明确量化结果。",
        innovation="提出新的科学建模流程。",
        scientific_significance="原文未说明。",
        resources="https://example.org/paper",
        model="legacy-model",
        cost_usd=0.01,
        assessment=None,
    )
    return replace(value, **changes)


def _store(
    storage: Storage,
    url: str,
    *,
    score: int = 8,
    content: str = "包含足够方法细节的原始正文。" * 30,
    summarized_at: str = "2026-09-05T00:00:00+00:00",
    summary: AI4SSummary | None = None,
) -> None:
    storage.record_items([
        Item(
            url=url,
            title=url,
            content=content,
            source="rss:test",
            published_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
    ])
    storage.save_analyzer_result(
        url,
        AnalyzerResult(
            is_ai4s=True,
            primary_category="biology",
            secondary_categories=[],
            content_type="paper",
            score=score,
            tags=["test"],
            model="test-analyzer",
            cost_usd=0.0,
        ),
    )
    storage.save_ai4s_summary(url, summary or _summary())
    storage._conn_or_die().execute(
        "UPDATE ai4s_analyses SET summarized_at=?, surfaced_at=? WHERE url=?",
        (summarized_at, "2026-09-05T01:00:00+00:00", url),
    )
    storage._conn_or_die().commit()


def _response(**changes) -> dict[str, str]:
    value = {
        "scientific_problem": "模型如何提高科学预测可靠性。",
        "ai_method": "采用结合领域约束的神经网络。",
        "main_result": "",
        "innovation": "把领域约束直接加入训练目标。",
        "scientific_significance": "可缩短科学候选验证流程。",
        "assessment": (
            "该方法把领域约束纳入学习过程，对提升科学建模可靠性具有明确价值。"
            "现有材料尚不足以判断跨数据集泛化能力，后续应重点核验独立基准表现。"
        ),
    }
    value.update(changes)
    return value


@pytest.fixture
def storage(tmp_path: Path):
    value = Storage(tmp_path / "enrichment.db")
    value.init()
    yield value
    value.close()


@pytest.mark.asyncio
async def test_enrichment_fills_only_missing_fields_and_preserves_state(
    storage: Storage, monkeypatch
):
    _store(storage, "https://legacy")
    before = storage._conn_or_die().execute(
        "SELECT summarized_at,surfaced_at,summarizer_model,summarizer_cost_usd "
        "FROM ai4s_analyses WHERE url='https://legacy'"
    ).fetchone()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.ai4s_summary_enrichment.enrich_item_content",
        AsyncMock(return_value=EnrichedContent("enriched evidence", 10, 17, "test")),
    )
    complete = AsyncMock(return_value=(_response(scientific_problem="不得覆盖"), 0.002))
    monkeypatch.setattr("src.ai4s_summary_enrichment.complete_json", complete)

    first = await run_ai4s_summary_enrichment(storage, _config(), limit=30)
    updated = storage.get_ai4s_analysis("https://legacy")
    second = await run_ai4s_summary_enrichment(storage, _config(), limit=30)
    after = storage._conn_or_die().execute(
        "SELECT summarized_at,surfaced_at,summarizer_model,summarizer_cost_usd "
        "FROM ai4s_analyses WHERE url='https://legacy'"
    ).fetchone()

    assert first == {
        "candidates": 1, "selected": 1, "enriched": 1,
        "qualified_after": 1, "errors": 0, "cost_usd": pytest.approx(0.002),
    }
    assert second["selected"] == second["enriched"] == 0
    assert complete.await_count == 1
    assert complete.await_args.kwargs["max_attempts"] == 1
    assert updated.summary.scientific_problem == "研究明确的科学问题。"
    assert updated.summary.scientific_significance == "可缩短科学候选验证流程。"
    assert updated.summary.assessment == _response()["assessment"]
    assert has_sufficient_information(updated)
    assert tuple(after[:3]) == tuple(before[:3])
    assert after[3] == pytest.approx(before[3] + 0.002)
    prompt = complete.await_args.kwargs["prompt"]
    assert "enriched evidence" in prompt
    assert "研究明确的科学问题" in prompt


@pytest.mark.asyncio
async def test_unrecoverable_summary_is_assessed_once_but_remains_filtered(
    storage: Storage, monkeypatch
):
    sparse = _summary(
        scientific_problem="原文未说明。",
        ai_method="信息不足。",
        innovation="原文未说明。",
    )
    _store(storage, "https://sparse", summary=sparse)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.ai4s_summary_enrichment.enrich_item_content",
        AsyncMock(return_value=EnrichedContent("short evidence", 14, 14)),
    )
    complete = AsyncMock(return_value=(_response(
        scientific_problem="", ai_method="", innovation="",
        scientific_significance="",
    ), 0.001))
    monkeypatch.setattr("src.ai4s_summary_enrichment.complete_json", complete)

    first = await run_ai4s_summary_enrichment(storage, _config(), limit=30)
    second = await run_ai4s_summary_enrichment(storage, _config(), limit=30)

    assert first["enriched"] == 1
    assert first["qualified_after"] == 0
    assert second["selected"] == 0
    assert complete.await_count == 1
    assert not has_sufficient_information(storage.get_ai4s_analysis("https://sparse"))


@pytest.mark.asyncio
async def test_enrichment_limit_error_isolation_and_recent_window(
    storage: Storage, monkeypatch
):
    _store(storage, "https://recoverable", score=8)
    _store(storage, "https://error", score=7)
    _store(
        storage,
        "https://old",
        score=10,
        summarized_at="2026-08-20T00:00:00+00:00",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.ai4s_summary_enrichment.enrich_item_content",
        AsyncMock(return_value=EnrichedContent("evidence", 8, 8)),
    )
    complete = AsyncMock(side_effect=[(_response(), 0.001), RuntimeError("boom")])
    monkeypatch.setattr("src.ai4s_summary_enrichment.complete_json", complete)

    result = await run_ai4s_summary_enrichment(storage, _config(), limit=2)

    assert result["candidates"] == 2
    assert result["selected"] == 2
    assert result["enriched"] == 1
    assert result["errors"] == 1
    assert result["cost_usd"] == pytest.approx(0.001)
    assert storage.get_ai4s_analysis("https://old").summary.assessment is None


@pytest.mark.asyncio
async def test_enrichment_rejects_out_of_range_limit(storage: Storage):
    with pytest.raises(ValueError, match="between 1 and 30"):
        await run_ai4s_summary_enrichment(storage, _config(), limit=31)
