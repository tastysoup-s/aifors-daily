import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.ai4s_daily import daily_period, generate_daily_report
from src.config import Config
from src.information_sufficiency import (
    has_sufficient_information,
    information_score,
    insufficient_information_reason,
    is_substantive,
)
from src.main import _parse_args
from src.models import AI4SSummary, AnalyzerResult, Item
from src.notifier.ai4s_web import render_ai4s_site
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
    summary: AI4SSummary | None = None,
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
        storage.save_ai4s_summary(url, summary if summary is not None else _summary())
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


@pytest.mark.parametrize("text", [
    None, "", " \n ", "原文未说明。", "信息不足", "暂无足够信息！",
    "当前来源仅提供简短介绍", "原文未披露明确量化结果。",
    "原文未披露明确的量化结果或实验数据。",
    "原文未说明训练数据，未披露具体模型或算法。",
    "原文未披露明确量化结果。仅提供项目名称和描述性标题，无实验数据或性能指标。",
])
def test_placeholder_fields_are_not_substantive(text):
    assert not is_substantive(text)


@pytest.mark.parametrize("text", [
    "采用E(3)-等变神经网络构建原子间势函数模型。",
    "原文未说明训练数据，但给出了完整实验结果",
    "原文未披露明确量化结果。定性结果表明模型能区分两类细胞。",
    "原文未披露模型细节；采用树表示组织细胞群体。",
    "准确率提高16.3%，训练数据原文未说明。",
    "MPNN",
])
def test_factual_clause_survives_missing_information_caveat(text):
    assert is_substantive(text)


def test_expanded_title_only_weather_summary_is_still_sparse(storage: Storage):
    summary = replace(
        _summary(),
        scientific_problem=(
            "原文未说明。标题仅提及该模型为全球天气AI模型，"
            "未说明其具体要解决的科学问题。"
        ),
        ai_method=(
            "原文未说明。标题仅提及模型名称为WeatherNext 3，"
            "未披露其具体AI方法、模型架构或训练设计。"
        ),
        main_result=(
            "原文未披露明确量化结果。标题仅称其为最先进、最准确的全球天气AI模型，"
            "但未提供任何性能指标、对比数据或实验条件。"
        ),
        innovation=(
            "原文未说明。标题仅称其为最先进、最准确，"
            "未具体说明相对已有方法的新颖之处。"
        ),
    )
    _add_analysis(storage, "https://WeatherNext-expanded", summary=summary)
    analysis = storage.get_ai4s_analysis("https://WeatherNext-expanded")
    assert information_score(analysis) == 0
    assert insufficient_information_reason(analysis) == "insufficient factual fields"


@pytest.mark.parametrize("facts, expected_score, reason", [
    (("原文未说明", "原文未说明", "原文未披露明确量化结果", "原文未说明"),
     0, "insufficient factual fields"),
    (("原子间相互作用", "E(3)-等变神经网络", "原文未说明", "等变原子间势"),
     3, None),
    (("研究问题", "信息不足", "信息不足", "创新点"), 2, "no method or result"),
    (("信息不足", "方法", "结果", "信息不足"), 2, None),
    (("信息不足", "方法", "信息不足", "信息不足"), 1, "insufficient factual fields"),
])
def test_information_rule_uses_only_four_factual_fields(
    storage: Storage, facts, expected_score, reason
):
    summary = replace(
        _summary(),
        scientific_problem=facts[0], ai_method=facts[1],
        main_result=facts[2], innovation=facts[3],
        assessment="很长的分析研判" * 1000,
        scientific_significance="丰富科学意义" * 1000,
        resources="https://example.org/paper",
    )
    _add_analysis(storage, "https://facts", summary=summary)
    analysis = storage.get_ai4s_analysis("https://facts")
    assert information_score(analysis) == expected_score
    assert insufficient_information_reason(analysis) == reason
    assert has_sufficient_information(analysis) is (reason is None)
    analysis.item.content = "正文宣传" * 3000
    assert information_score(analysis) == expected_score


def _sparse_summary():
    return replace(
        _summary(), scientific_problem="原文未说明", ai_method="原文未说明",
        main_result="原文未披露明确量化结果", innovation="原文未说明",
        assessment="很长的分析研判" * 100,
    )


@pytest.mark.parametrize("qualified_count", [0, 2])
def test_daily_never_refills_sparse_items_and_preserves_database(
    storage: Storage, caplog, qualified_count, tmp_path: Path
):
    _add_analysis(storage, "https://WeatherNext", score=10, summary=_sparse_summary())
    for index in range(qualified_count):
        _add_analysis(storage, f"https://qualified-{index}")
    connection = storage._conn_or_die()
    before = {table: connection.execute(f"SELECT * FROM {table}").fetchall()
              for table in ("items", "ai4s_analyses")}
    with caplog.at_level("INFO"):
        result = generate_daily_report(
            storage, Config(sources=[], keywords=[], top_n=10), REPORT_DATE
        )
    assert result["candidates"] == qualified_count + 1
    assert result["qualified"] == result["selected"] == qualified_count
    assert result["filtered_sparse"] == 1
    assert all(item.analysis.item.url != "https://WeatherNext"
               for item in storage.get_daily_report(REPORT_DATE).items)
    for table, rows in before.items():
        assert connection.execute(f"SELECT * FROM {table}").fetchall() == rows
    assert f"qualified={qualified_count} filtered_sparse=1 selected={qualified_count}" in caplog.text
    assert "https://WeatherNext reason=insufficient factual fields information_score=0" in caplog.text
    rendered = render_ai4s_site(storage, output_dir=tmp_path / "site")
    assert rendered["daily_items"] == qualified_count
    assert (tmp_path / "site" / "index.html").is_file()


def test_filter_runs_before_top_n_and_source_diversity(storage: Storage, caplog):
    _add_analysis(storage, "https://sparse-high", score=10, summary=_sparse_summary())
    _add_analysis(storage, "https://arxiv-high", score=9, source="arxiv:arxiv-ai-methods")
    _add_analysis(storage, "https://arxiv-tie", source="arxiv:arxiv-ai-methods")
    _add_analysis(storage, "https://biorxiv", source="rss:biorxiv-ai4s")
    _add_analysis(storage, "https://github-low", score=7, source="github:github-ai-for-science")
    _add_analysis(storage, "https://no-method", score=9,
                  summary=replace(_summary(), ai_method="信息不足", main_result="信息不足"))
    with caplog.at_level("INFO"):
        result = generate_daily_report(
            storage, Config(sources=[], keywords=[], top_n=3), REPORT_DATE
        )
    assert [item.analysis.item.url for item in storage.get_daily_report(REPORT_DATE).items] == [
        "https://arxiv-high", "https://biorxiv", "https://arxiv-tie",
    ]
    assert result["qualified"] == 4
    assert result["filtered_sparse"] == 2
    assert "https://no-method reason=no method or result information_score=2" in caplog.text


def test_existing_daily_selection_is_not_rewritten(storage: Storage):
    _add_analysis(storage, "https://old-sparse", summary=_sparse_summary())
    start, end = daily_period(REPORT_DATE)
    original, _ = storage.create_report(
        "daily", start, end, storage.get_report_candidates(start, end, min_score=7)
    )
    result = generate_daily_report(storage, Config(sources=[], keywords=[]), REPORT_DATE)
    assert result["created"] is False
    assert result["qualified"] == 0
    assert result["filtered_sparse"] == 1
    assert result["selected"] == 1  # Existing selection is immutable, even if sparse.
    assert storage.get_daily_report(REPORT_DATE) == original


def test_missing_summary_is_insufficient(storage: Storage):
    _add_analysis(storage, "https://no-summary", summarized=False)
    assert not has_sufficient_information(storage.get_ai4s_analysis("https://no-summary"))
