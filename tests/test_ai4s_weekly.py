from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ai4s_daily import generate_daily_report
from src.ai4s_weekly import (
    WEEKLY_CANDIDATE_LIMIT,
    generate_weekly_report,
    latest_weekly_report_date,
    select_representative_works,
    weekly_period,
)
from src.config import Config, Models
from src.llm import LLMError
from src.main import _parse_args
from src.models import AI4SAnalysis, AI4SSummary, AnalyzerResult, Item
from src.storage import Storage


def _config(*, threshold: int = 7, top_n: int = 10) -> Config:
    return Config(
        sources=[],
        keywords=[],
        models=Models(
            scorer="deepseek/deepseek-chat",
            summarizer="deepseek/deepseek-chat",
        ),
        score_threshold=threshold,
        top_n=top_n,
    )


def _summary() -> AI4SSummary:
    return AI4SSummary(
        scientific_problem="研究一个明确的科学问题。",
        ai_method="使用机器学习模型辅助科学计算。",
        main_result="原文报告了验证结果。",
        innovation="结合数据驱动方法与领域知识。",
        scientific_significance="为后续实验提供候选方向。",
        resources="原文未提供额外资源。",
        model="test-summarizer",
        cost_usd=0.001,
    )


def _analysis(
    url: str,
    *,
    category: str = "biology",
    score: int = 8,
    published_at: datetime | None = None,
) -> AI4SAnalysis:
    return AI4SAnalysis(
        item=Item(
            url=url,
            title=f"Work {url}",
            content="RAW-CONTENT-MUST-NOT-BE-SENT",
            published_at=published_at or datetime(2026, 9, 2, tzinfo=timezone.utc),
            source="test",
        ),
        analyzer=AnalyzerResult(
            is_ai4s=True,
            primary_category=category,
            secondary_categories=[],
            content_type="paper",
            score=score,
            tags=["scientific-ml"],
            model="test-analyzer",
            cost_usd=0.001,
        ),
        summary=_summary(),
    )


def _store(
    storage: Storage,
    url: str,
    *,
    summarized_at: datetime | None,
    category: str = "biology",
    score: int = 8,
    is_ai4s: bool = True,
    published_at: datetime | None = None,
) -> None:
    analysis = _analysis(
        url,
        category=category,
        score=score,
        published_at=published_at,
    )
    if not is_ai4s:
        analysis.analyzer = AnalyzerResult(
            is_ai4s=False,
            primary_category=None,
            secondary_categories=[],
            content_type="research_news",
            score=score,
            tags=[],
            model="test-analyzer",
            cost_usd=0.0,
        )
    storage.record_items([analysis.item])
    storage.save_analyzer_result(url, analysis.analyzer)
    if summarized_at is not None:
        storage.save_ai4s_summary(url, analysis.summary)
        storage._conn_or_die().execute(
            "UPDATE ai4s_analyses SET summarized_at=? WHERE url=?",
            (summarized_at.isoformat(), url),
        )
        storage._conn_or_die().commit()


def _weekly_response(**overrides) -> dict:
    response = {
        "overview": "本期工作共同关注以机器学习缩短科学候选筛选周期。",
        "category_trends": {
            "biology": "多项工作把领域知识加入数据驱动建模。",
        },
        "watchlist": ["领域约束与数据驱动模型的结合"],
    }
    response.update(overrides)
    return response


def test_wednesday_period_covers_monday_through_wednesday_utc():
    start, end = weekly_period(date(2026, 9, 2))

    assert start == datetime(2026, 8, 31, tzinfo=timezone.utc)
    assert end.date() == date(2026, 9, 2)
    assert end.hour == 23 and end.minute == 59


def test_sunday_period_covers_thursday_through_sunday_utc():
    start, end = weekly_period(date(2026, 9, 6))

    assert start == datetime(2026, 9, 3, tzinfo=timezone.utc)
    assert end.date() == date(2026, 9, 6)


def test_invalid_manual_weekday_is_rejected():
    with pytest.raises(ValueError, match="Wednesday or Sunday"):
        weekly_period(date(2026, 9, 3))
    with pytest.raises(SystemExit):
        _parse_args(["generate-weekly", "--report-date", "2026-09-03"])


@pytest.mark.parametrize(
    ("current_date", "expected"),
    [
        (date(2026, 9, 3), date(2026, 9, 2)),
        (date(2026, 9, 7), date(2026, 9, 6)),
        (date(2026, 9, 6), date(2026, 9, 6)),
    ],
)
def test_default_date_uses_latest_wednesday_or_sunday(current_date, expected):
    assert latest_weekly_report_date(current_date) == expected


def test_candidate_filters_and_ordering(tmp_path: Path):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    in_period = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    _store(storage, "https://high-new", summarized_at=in_period, score=9)
    _store(
        storage,
        "https://high-old",
        summarized_at=in_period,
        score=9,
        published_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )
    _store(storage, "https://low", summarized_at=in_period, score=6)
    _store(storage, "https://not-ai4s", summarized_at=in_period, is_ai4s=False)
    _store(storage, "https://unsummarized", summarized_at=None)
    _store(
        storage,
        "https://outside",
        summarized_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        score=10,
    )

    start, end = weekly_period(date(2026, 9, 2))
    candidates = storage.get_report_candidates(start, end, min_score=7, limit=30)

    assert [item.item.url for item in candidates] == [
        "https://high-new",
        "https://high-old",
    ]
    storage.close()


def test_representatives_use_category_top_items_then_global_top():
    candidates = [
        _analysis("https://bio-1", category="biology", score=10),
        _analysis("https://material-1", category="materials", score=9),
        _analysis("https://bio-2", category="biology", score=8),
        _analysis("https://material-2", category="materials", score=7),
        _analysis("https://bio-3", category="biology", score=7),
        _analysis("https://bio-4", category="biology", score=7),
    ]

    selected = select_representative_works(candidates)

    assert [item.item.url for item in selected] == [
        "https://bio-1",
        "https://material-1",
        "https://bio-2",
        "https://material-2",
        "https://bio-3",
    ]


def test_representatives_preserve_every_present_category():
    categories = [
        "biology",
        "medicine",
        "chemistry",
        "materials",
        "physics",
        "earth",
        "general",
    ]
    candidates = [
        _analysis(f"https://{category}-high", category=category, score=10 - index)
        for index, category in enumerate(categories)
    ] + [
        _analysis(f"https://{category}-second", category=category, score=3)
        for category in categories
    ]

    selected = select_representative_works(candidates)

    assert len(selected) == 10
    assert {item.analyzer.primary_category for item in selected} == set(categories)


@pytest.mark.asyncio
async def test_valid_weekly_synthesis_persists_output_cost_and_concise_input(
    monkeypatch, tmp_path: Path
):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    _store(
        storage,
        "https://biology",
        summarized_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock(return_value=(_weekly_response(), 0.0042))
    monkeypatch.setattr("src.ai4s_weekly.complete_json", complete)

    metrics = await generate_weekly_report(storage, _config(), date(2026, 9, 2))
    report = storage.get_latest_weekly_report()

    assert metrics["candidates"] == 1
    assert metrics["representatives"] == 1
    assert metrics["llm_calls"] == 1
    assert report is not None
    assert report.overview.startswith("本期工作")
    assert report.category_trends == _weekly_response()["category_trends"]
    assert report.watchlist == _weekly_response()["watchlist"]
    assert report.model == "deepseek/deepseek-chat"
    assert report.cost_usd == pytest.approx(0.0042)
    prompt = complete.await_args.kwargs["prompt"]
    assert "scientific_problem" in prompt
    assert "RAW-CONTENT-MUST-NOT-BE-SENT" not in prompt
    storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"category_trends": {}, "watchlist": []}, "overview"),
        (_weekly_response(category_trends=[]), "category_trends"),
        (_weekly_response(category_trends={"astronomy": "趋势"}), "invalid category"),
        (_weekly_response(category_trends={"materials": "趋势"}), "without candidates"),
        (_weekly_response(watchlist="方向"), "watchlist"),
    ],
)
async def test_invalid_weekly_json_is_rejected(
    monkeypatch, tmp_path: Path, response: dict, message: str
):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    _store(
        storage,
        "https://biology",
        summarized_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.ai4s_weekly.complete_json",
        AsyncMock(return_value=(response, 0.001)),
    )

    with pytest.raises(LLMError, match=message):
        await generate_weekly_report(storage, _config(), date(2026, 9, 2))
    start, end = weekly_period(date(2026, 9, 2))
    assert storage.get_weekly_report(start, end) is None
    storage.close()


@pytest.mark.asyncio
async def test_llm_failure_does_not_persist_report(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    _store(
        storage,
        "https://biology",
        summarized_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.ai4s_weekly.complete_json",
        AsyncMock(side_effect=LLMError("provider failure")),
    )

    with pytest.raises(LLMError, match="provider failure"):
        await generate_weekly_report(storage, _config(), date(2026, 9, 2))
    assert storage.get_latest_weekly_report() is None
    storage.close()


@pytest.mark.asyncio
async def test_empty_period_creates_report_without_llm_or_key(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    complete = AsyncMock()
    monkeypatch.setattr("src.ai4s_weekly.complete_json", complete)

    metrics = await generate_weekly_report(storage, _config(), date(2026, 9, 2))

    assert metrics["created"] is True
    assert metrics["candidates"] == 0
    assert metrics["llm_calls"] == 0
    assert complete.await_count == 0
    assert storage.get_latest_weekly_report().items == []
    storage.close()


@pytest.mark.asyncio
async def test_same_slot_is_idempotent_without_second_llm_call(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    _store(
        storage,
        "https://biology",
        summarized_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock(return_value=(_weekly_response(), 0.002))
    monkeypatch.setattr("src.ai4s_weekly.complete_json", complete)

    first = await generate_weekly_report(storage, _config(), date(2026, 9, 2))
    second = await generate_weekly_report(storage, _config(), date(2026, 9, 2))

    assert first["report_id"] == second["report_id"]
    assert second["created"] is False
    assert second["llm_calls"] == 0
    assert second["cost_usd"] == 0.0
    assert complete.await_count == 1
    assert len(storage.get_report_items(first["report_id"])) == 1
    storage.close()


@pytest.mark.asyncio
async def test_candidate_preselection_is_capped_at_thirty(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    summarized_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for index in range(WEEKLY_CANDIDATE_LIMIT + 2):
        _store(storage, f"https://work-{index:02d}", summarized_at=summarized_at)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock(return_value=(_weekly_response(), 0.001))
    monkeypatch.setattr("src.ai4s_weekly.complete_json", complete)

    metrics = await generate_weekly_report(storage, _config(), date(2026, 9, 2))

    assert metrics["candidates"] == WEEKLY_CANDIDATE_LIMIT
    prompt = complete.await_args.kwargs["prompt"]
    assert prompt.count('"title":') == WEEKLY_CANDIDATE_LIMIT
    assert "RAW-CONTENT-MUST-NOT-BE-SENT" not in prompt
    storage.close()


@pytest.mark.asyncio
async def test_same_article_can_appear_in_daily_and_weekly(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "weekly.db")
    storage.init()
    _store(
        storage,
        "https://shared",
        summarized_at=datetime(2026, 9, 2, 12, tzinfo=timezone.utc),
    )
    daily = generate_daily_report(storage, _config(), date(2026, 9, 2))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.ai4s_weekly.complete_json",
        AsyncMock(return_value=(_weekly_response(), 0.001)),
    )

    weekly = await generate_weekly_report(storage, _config(), date(2026, 9, 2))

    assert storage.get_report_items(daily["report_id"])[0].analysis.item.url == "https://shared"
    assert storage.get_report_items(weekly["report_id"])[0].analysis.item.url == "https://shared"
    storage.close()


def test_weekly_cli_accepts_valid_slot_and_has_db_option():
    args = _parse_args([
        "generate-weekly",
        "--db",
        "data/ai4s_dev.db",
        "--report-date",
        "2026-09-06",
    ])

    assert args.db == "data/ai4s_dev.db"
    assert args.report_date == date(2026, 9, 6)
