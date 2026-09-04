import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.ai4s_daily import daily_period, generate_daily_report
from src.config import Config
from src.main import _parse_args
from src.models import AI4SSummary, AnalyzerResult, Item
from src.storage import Storage


REPORT_DATE = date(2026, 9, 3)


def _summary() -> AI4SSummary:
    return AI4SSummary(
        scientific_problem="problem",
        ai_method="method",
        main_result="result",
        innovation="innovation",
        scientific_significance="significance",
        resources="resource",
        model="test-summary",
        cost_usd=0.01,
    )


def _add_analysis(
    storage: Storage,
    url: str,
    *,
    score: int = 8,
    is_ai4s: bool = True,
    category: str = "biology",
    source: str = "test",
    summarized: bool = True,
    summarized_at: str = "2026-09-03T12:00:00+00:00",
    published_at: str = "2026-09-03T10:00:00+00:00",
) -> None:
    storage.record_items([
        Item(
            url=url,
            title=url,
            content="content",
            source=source,
            published_at=datetime.fromisoformat(published_at),
        )
    ])
    storage.save_analyzer_result(
        url,
        AnalyzerResult(
            is_ai4s=is_ai4s,
            primary_category=category if is_ai4s else None,
            secondary_categories=[],
            content_type="paper",
            score=score,
            tags=["tag"],
            model="test-analyzer",
            cost_usd=0.01,
        ),
    )
    if summarized:
        storage.save_ai4s_summary(url, _summary())
        storage._conn_or_die().execute(
            "UPDATE ai4s_analyses SET summarized_at=? WHERE url=?",
            (summarized_at, url),
        )
        storage._conn_or_die().commit()


@pytest.fixture
def storage(tmp_path: Path):
    value = Storage(tmp_path / "test.db")
    value.init()
    yield value
    value.close()


def test_fresh_db_creates_report_tables(storage: Storage):
    tables = {
        row["name"]
        for row in storage._conn_or_die().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"items", "summaries", "ai4s_analyses", "reports", "report_items"} <= tables


def test_existing_db_adds_report_tables_without_losing_data(tmp_path: Path):
    db_path = tmp_path / "existing.db"
    storage = Storage(db_path)
    storage.init()
    _add_analysis(storage, "https://existing")
    storage._conn_or_die().executescript("DROP TABLE report_items; DROP TABLE reports;")
    storage.close()

    reopened = Storage(db_path)
    reopened.init()
    assert reopened.fetch_item_row("https://existing")["title"] == "https://existing"
    assert reopened.get_ai4s_analysis("https://existing") is not None
    assert reopened._conn_or_die().execute(
        "SELECT count(*) FROM sqlite_master"
        " WHERE type='table' AND name IN ('reports','report_items')"
    ).fetchone()[0] == 2
    reopened.close()


def test_daily_period_is_one_utc_day():
    start, end = daily_period(REPORT_DATE)
    assert start == datetime(2026, 9, 3, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 3, 23, 59, 59, 999999, tzinfo=timezone.utc)


def test_daily_filters_orders_limits_and_preserves_category(storage: Storage):
    _add_analysis(storage, "https://score-8-old", score=8, category="biology")
    _add_analysis(
        storage,
        "https://score-8-new",
        score=8,
        category="materials",
        published_at="2026-09-03T11:00:00+00:00",
    )
    _add_analysis(storage, "https://score-9", score=9, category="chemistry")
    _add_analysis(storage, "https://low", score=6)
    _add_analysis(storage, "https://non-ai4s", is_ai4s=False)
    _add_analysis(storage, "https://unsummarized", summarized=False)
    _add_analysis(
        storage,
        "https://outside",
        summarized_at="2026-09-02T23:59:59+00:00",
    )

    result = generate_daily_report(
        storage,
        Config(sources=[], keywords=[], score_threshold=7, top_n=2),
        REPORT_DATE,
    )
    report = storage.get_daily_report(REPORT_DATE)

    assert result["candidates"] == 3
    assert result["selected"] == 2
    assert report is not None
    assert [item.analysis.item.url for item in report.items] == [
        "https://score-9",
        "https://score-8-new",
    ]
    assert [item.category for item in report.items] == ["chemistry", "materials"]


def test_daily_diversifies_exact_score_ties_without_crossing_score_tiers(
    storage: Storage,
):
    _add_analysis(
        storage,
        "https://score-9-arxiv",
        score=9,
        source="arxiv:arxiv-ai-methods",
        published_at="2026-09-03T12:00:00+00:00",
    )
    _add_analysis(
        storage,
        "https://score-8-arxiv-new",
        score=8,
        source="arxiv:arxiv-ai-methods",
        published_at="2026-09-03T11:00:00+00:00",
    )
    _add_analysis(
        storage,
        "https://score-8-arxiv-old",
        score=8,
        source="arxiv:arxiv-biology-medicine",
        published_at="2026-09-03T10:00:00+00:00",
    )
    _add_analysis(
        storage,
        "https://score-8-biorxiv",
        score=8,
        source="rss:biorxiv-ai4s",
        published_at="2026-09-03T09:00:00+00:00",
    )
    _add_analysis(
        storage,
        "https://score-7-medrxiv",
        score=7,
        source="rss:medrxiv-ai4s",
        published_at="2026-09-03T13:00:00+00:00",
    )

    result = generate_daily_report(
        storage,
        Config(sources=[], keywords=[], score_threshold=7, top_n=3),
        REPORT_DATE,
    )
    report = storage.get_daily_report(REPORT_DATE)

    assert report is not None
    assert [item.analysis.item.url for item in report.items] == [
        "https://score-9-arxiv",
        "https://score-8-biorxiv",
        "https://score-8-arxiv-new",
    ]
    assert "https://score-7-medrxiv" not in {
        item.analysis.item.url for item in report.items
    }
    assert result["unique_sources"] == 2
    assert result["source_families"] == 2


def test_daily_logs_source_diversity(storage: Storage, caplog):
    _add_analysis(
        storage,
        "https://arxiv",
        source="arxiv:arxiv-ai-methods",
    )
    _add_analysis(
        storage,
        "https://github",
        source="github:github-ai-for-science",
    )

    with caplog.at_level("INFO"):
        generate_daily_report(
            storage,
            Config(sources=[], keywords=[], score_threshold=7, top_n=10),
            REPORT_DATE,
        )

    assert any(
        "items=2 unique_sources=2 source_families=2" in record.message
        for record in caplog.records
    )
    assert any("arXiv=1" in record.message for record in caplog.records)
    assert any("GitHub=1" in record.message for record in caplog.records)


def test_empty_daily_report_is_persisted(storage: Storage):
    result = generate_daily_report(
        storage, Config(sources=[], keywords=[]), REPORT_DATE
    )
    report = storage.get_latest_daily_report()
    assert result["selected"] == 0
    assert report is not None
    assert report.items == []
    assert report.model is None
    assert report.cost_usd == 0


def test_daily_generation_is_idempotent_without_duplicate_items(storage: Storage):
    _add_analysis(storage, "https://a")
    cfg = Config(sources=[], keywords=[])

    first = generate_daily_report(storage, cfg, REPORT_DATE)
    second = generate_daily_report(storage, cfg, REPORT_DATE)

    assert first["report_id"] == second["report_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert storage._conn_or_die().execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    assert storage._conn_or_die().execute("SELECT count(*) FROM report_items").fetchone()[0] == 1


def test_report_item_primary_key_rejects_duplicates(storage: Storage):
    _add_analysis(storage, "https://a")
    generate_daily_report(storage, Config(sources=[], keywords=[]), REPORT_DATE)
    report_id = storage.get_daily_report(REPORT_DATE).id
    with pytest.raises(sqlite3.IntegrityError):
        storage._conn_or_die().execute(
            "INSERT INTO report_items VALUES (?, ?, ?, ?, ?)",
            (report_id, "https://a", 2, "biology", None),
        )


def test_daily_cli_accepts_date_and_rejects_invalid_date():
    args = _parse_args([
        "generate-daily", "--db", "data/ai4s_dev.db", "--report-date", "2026-09-03"
    ])
    assert args.report_date == REPORT_DATE
    with pytest.raises(SystemExit):
        _parse_args(["generate-daily", "--report-date", "09/03/2026"])
