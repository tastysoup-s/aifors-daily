from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from src.ai4s_daily import generate_daily_report, select_daily_candidates
from src.ai4s_summarizer import summarize_analysis
from src.ai4s_weekly import (
    _render_weekly_prompt, select_representative_works,
    select_weekly_synthesis_candidates, weekly_period,
)
from src.config import Config
from src.information_sufficiency import has_sufficient_information
from src.llm import LLMError
from src.storage import Storage
from tests.test_ai4s_daily import _add_analysis, _summary, REPORT_DATE
from tests.test_ai4s_summarizer import _analysis as summary_analysis, _config, _summary_response
from tests.test_ai4s_weekly import _analysis, _store


@pytest.mark.parametrize("dominant", ["biology", "physics", "earth"])
def test_generic_soft_cap_with_sufficient_alternatives(dominant):
    alternatives = [c for c in ("biology", "medicine", "chemistry", "materials", "physics", "earth") if c != dominant]
    pool = [_analysis(f"https://dominant-{i}", category=dominant, score=10) for i in range(8)]
    pool += [_analysis(f"https://{c}-{i}", category=c) for c in alternatives[:4] for i in range(2)]
    selected = select_daily_candidates(pool, 10)
    assert len(selected) == 10
    assert Counter(a.analyzer.primary_category for a in selected)[dominant] == 3
    assert all(has_sufficient_information(a) for a in selected)


@pytest.mark.parametrize("counts", [
    {"biology": 10, "medicine": 6, "materials": 4, "physics": 3, "chemistry": 2},
    # Backfill must leave a slot for a second domain even at a lower quality tier.
    {"biology": 10, "medicine": 6, "materials": 4, "physics": 1},
])
def test_biomedical_group_reserves_three_and_two_other_domains(counts):
    pool = [_analysis(f"https://{c}-{i}", category=c,
                      score=10 if c in {"biology", "medicine"} else 8 if c == "materials" else 7)
            for c, n in counts.items() for i in range(n)]
    selected = select_daily_candidates(pool, 10)
    domains = Counter(a.analyzer.primary_category for a in selected)
    assert len(selected) == 10
    assert domains["biology"] + domains["medicine"] <= 7
    assert sum(n for c, n in domains.items() if c not in {"biology", "medicine"}) >= 3
    assert len(set(domains) - {"biology", "medicine"}) >= 2


def test_group_reservation_backfills_when_only_two_other_items_qualify():
    pool = [_analysis(f"https://bio-{i}", score=10) for i in range(16)]
    pool += [_analysis(f"https://{c}", category=c, score=7) for c in ("materials", "physics")]
    sparse = _analysis("https://sparse-chemistry", category="chemistry", score=10)
    sparse.summary = replace(sparse.summary, ai_method="信息不足")
    selected = select_daily_candidates([sparse, *pool], 10)
    assert Counter(a.analyzer.primary_category for a in selected) == {"biology": 8, "materials": 1, "physics": 1}
    assert all(has_sufficient_information(a) for a in selected)


def test_daily_twelve_enforces_multidomain_targets_when_pool_allows():
    counts = {"biology": 10, "medicine": 6, "chemistry": 4, "materials": 4,
              "physics": 4, "earth": 4, "general": 2}
    pool = [_analysis(f"https://{category}-{index}", category=category,
                      score=10 if category in {"biology", "medicine"} else 7)
            for category, count in counts.items() for index in range(count)]
    selected = select_daily_candidates(pool, 12)
    distribution = Counter(a.analyzer.primary_category for a in selected)
    assert len(selected) == 12
    assert max(distribution.values()) <= 3
    assert distribution["biology"] + distribution["medicine"] <= 5
    assert sum(n for c, n in distribution.items() if c not in {"biology", "medicine"}) >= 7
    assert len(distribution) >= 5
    assert len(set(distribution) & {"chemistry", "materials", "physics", "earth"}) >= 3


def test_daily_twelve_never_uses_sparse_candidate_for_diversity():
    pool = [_analysis(f"https://bio-{i}", score=10) for i in range(12)]
    pool += [_analysis(f"https://{c}-{i}", category=c, score=7)
             for c in ("chemistry", "materials", "physics") for i in range(2)]
    sparse = _analysis("https://sparse-earth", category="earth", score=10)
    sparse.summary = replace(sparse.summary, ai_method="信息不足")
    selected = select_daily_candidates([sparse, *pool], 12)
    assert len(selected) == 12
    assert sparse not in selected


@pytest.mark.parametrize("limit,expected_cap", [(5, 2), (10, 3), (20, 6)])
def test_soft_cap_scales_with_limit(limit, expected_cap):
    pool = [_analysis(f"https://bio-{i}", score=10) for i in range(25)]
    pool += [_analysis(f"https://{c}-{i}", category=c) for c in ("medicine", "chemistry", "materials", "physics") for i in range(8)]
    selected = select_daily_candidates(pool, limit)
    assert len(selected) == limit
    assert sum(a.analyzer.primary_category == "biology" for a in selected) == expected_cap


def test_daily_fallback_fills_ten_without_admitting_sparse_items(tmp_path, caplog):
    storage = Storage(tmp_path / "daily.db")
    storage.init()
    for i in range(10):
        _add_analysis(storage, f"https://bio-{i}")
    for category in ("medicine", "chemistry"):
        _add_analysis(storage, f"https://{category}", category=category)
    _add_analysis(storage, "https://sparse", category="physics", score=10,
                  summary=replace(_summary(), scientific_significance="信息不足"))
    with caplog.at_level("INFO"):
        generate_daily_report(storage, Config(sources=[], keywords=[]), REPORT_DATE)
    report = storage.get_latest_daily_report()
    assert Counter(i.category for i in report.items) == {"biology": 8, "medicine": 1, "chemistry": 1}
    assert all(i.analysis.item.url != "https://sparse" for i in report.items)
    for label in ("candidate categories", "qualified categories", "selected categories", "selected source families"):
        assert label in caplog.text
    storage.close()


def test_weekly_uses_publication_dates_without_changing_daily_semantics(tmp_path):
    storage = Storage(tmp_path / "period.db")
    storage.init()
    start, end = weekly_period(date(2026, 9, 6))
    _store(storage, "https://old-newly-summarized", published_at=datetime(2020, 1, 1, tzinfo=timezone.utc), summarized_at=end)
    _store(storage, "https://new-summarized-later", published_at=start, summarized_at=datetime(2026, 9, 7, tzinfo=timezone.utc))
    _store(storage, "https://end", published_at=end, summarized_at=end)
    _store(storage, "https://future", published_at=datetime(2026, 9, 7, tzinfo=timezone.utc), summarized_at=end)
    _store(storage, "https://unknown", published_at=end, summarized_at=end)
    conn = storage._conn_or_die()
    conn.execute('UPDATE items SET raw_json=? WHERE url=?', ('{"publication_date_known":false}', "https://unknown"))
    # UTC conversion must handle offsets as well as stored UTC timestamps.
    _store(storage, "https://offset", published_at=datetime.fromisoformat("2026-08-31T00:30:00+08:00"), summarized_at=end)
    conn.commit()
    assert {a.item.url for a in storage.get_weekly_report_candidates(start, end, 7)} == {"https://new-summarized-later", "https://end"}
    daily_urls = {a.item.url for a in storage.get_report_candidates(start, end, 7)}
    assert "https://old-newly-summarized" in daily_urls
    assert "https://new-summarized-later" not in daily_urls
    storage.close()


def test_weekly_synthesis_covers_six_domains_and_filters_sparse_duplicates():
    categories = ["biology", "medicine", "chemistry", "materials", "physics", "earth"]
    pool = [_analysis(f"https://{c}-{i}", category=c, score=10 if c == "biology" else 8)
            for c in categories for i in range(15)]
    sparse = _analysis("https://sparse", category="general", score=10)
    sparse.summary = replace(sparse.summary, ai_method="原文未说明")
    selected = select_weekly_synthesis_candidates([sparse, *pool, pool[0]])
    assert len(selected) == len({a.item.url for a in selected}) == 30
    counts = Counter(a.analyzer.primary_category for a in selected)
    assert set(counts) == set(categories)
    assert counts["biology"] <= 9
    assert all(has_sufficient_information(a) for a in selected)


def test_weekly_evidence_prefers_papers_over_code_with_same_domain():
    pool = []
    for i, c in enumerate(("biology", "medicine", "chemistry", "materials", "physics", "earth")):
        paper = _analysis(f"https://paper-{i}", category=c, score=8)
        paper.item.source = "arxiv:test"
        code = _analysis(f"https://code-{i}", category=c, score=10)
        code.item.source = "github:test"
        code.analyzer.content_type = "tool"
        pool.extend([code, paper])
    selected = select_representative_works(pool)
    assert len(selected) == 6
    assert all(a.item.source == "arxiv:test" for a in selected)
    assert len(select_representative_works([pool[0]])) == 1  # normal ecosystem fallback


def test_weekly_prompt_requires_independent_evidence_and_single_signal_label():
    start, end = weekly_period(date(2026, 9, 6))
    prompt = _render_weekly_prompt([_analysis("https://one")], start, end)
    assert "只有 1 篇工作" in prompt and "单点信号" in prompt
    assert "多个相互独立工作" in prompt
    assert "不能按不同 URL 算作独立证据" in prompt
    assert "禁止重新逐篇复述" in prompt
    assert "不要连续点名超过 2 个具体工作" in prompt
    assert "防过度外推" in prompt
    assert "本期样本边界" in prompt and "当前观察窗口" in prompt
    assert "single-point signal" in prompt
    assert '"url": "https://one"' in prompt


@pytest.mark.parametrize("method", [
    "原文未说明使用AI/ML方法；该方法基于约束动力学。",
    "原文未明确说明使用AI/ML方法；研究采用穷举枚举与DFT。",
    "原文未明确披露AI/ML的具体方法细节，仅描述信号构建特征。",
    "原文未披露具体的AI/ML方法；研究使用了基因组学统计方法。",
])
def test_conventional_computation_cannot_fill_explicitly_missing_ai_method(method):
    analysis = _analysis("https://missing-ai")
    analysis.summary = replace(analysis.summary, ai_method=method)
    assert not has_sufficient_information(analysis)
    assert select_daily_candidates([analysis], 10) == []
    assert select_weekly_synthesis_candidates([analysis]) == []


def test_synthesis_preserves_research_evidence_when_ecosystem_scores_higher():
    code = [_analysis(f"https://code-{i}", score=10) for i in range(35)]
    for item in code:
        item.item.source = "github:test"
        item.analyzer.content_type = "tool"
    papers = [_analysis(f"https://paper-{i}", score=8) for i in range(6)]
    selected = select_weekly_synthesis_candidates(code + papers)
    assert len(selected) == 30
    assert {a.item.url for a in papers} <= {a.item.url for a in selected}
    assert all(a.analyzer.content_type == "paper" for a in select_representative_works(selected))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,limit", [("scientific_problem", 150), ("ai_method", 220), ("main_result", 260), ("innovation", 150), ("scientific_significance", 150), ("assessment", 120)])
async def test_new_summary_rejects_overlong_fields_without_retry(monkeypatch, field, limit):
    complete = AsyncMock(return_value=(_summary_response(**{field: "测" * (limit + 1)}), 0))
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)
    with pytest.raises(LLMError, match="length limits"):
        await summarize_analysis(summary_analysis(), _config())
    assert complete.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,length", [("scientific_problem", 120), ("main_result", 220), ("assessment", 100)])
async def test_summary_accepts_modest_variance_without_truncation(monkeypatch, field, length):
    value = "测" * length
    complete = AsyncMock(return_value=(_summary_response(**{field: value}), 0.002))
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)
    result = await summarize_analysis(summary_analysis(), _config())
    assert getattr(result, field) == value
    assert complete.await_count == 1
    assert result.cost_usd == pytest.approx(0.002)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["scientific_problem", "ai_method", "main_result", "scientific_significance"])
async def test_assessment_cannot_repeat_any_fact_field(monkeypatch, field):
    response = _summary_response()
    response["assessment"] = response[field]
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", AsyncMock(return_value=(response, 0)))
    with pytest.raises(LLMError, match="judgement"):
        await summarize_analysis(summary_analysis(), _config())


def test_v2_source_pool_and_workflow_budget_contract():
    sources = yaml.safe_load(Path("config/sources.yaml").read_text(encoding="utf-8"))["sources"]
    tavily = [s for s in sources if s["type"] == "tavily"]
    assert len(tavily) == 1 and len(tavily[0]["queries"]) == 7
    for category in ("chemistry", "materials", "physics", "earth"):
        source = next(s for s in sources if s["name"] == f"arxiv-{category}")
        assert "machine learning" in source["terms"]
    workflow = Path(".github/workflows/daily.yml").read_text(encoding="utf-8")
    assert "TAVILY_API_KEY: ${{ secrets.TAVILY_API_KEY }}" in workflow
    assert "tavily-budget" not in workflow
