from datetime import date, datetime, timezone
from pathlib import Path

from src.ai4s_daily import daily_period
from src.ai4s_weekly import weekly_period
from src.main import _parse_args
from src.models import AI4SAnalysis, AI4SSummary, AnalyzerResult, Item
from src.notifier.ai4s_web import render_ai4s_site
from src.storage import Storage


def _store_analysis(
    storage: Storage,
    url: str,
    *,
    title: str,
    category: str,
    content_type: str = "paper",
    score: int = 8,
) -> AI4SAnalysis:
    item = Item(
        url=url,
        title=title,
        content="raw content",
        published_at=datetime(2026, 9, 3, 8, tzinfo=timezone.utc),
        source="test-source",
    )
    analyzer = AnalyzerResult(
        is_ai4s=True,
        primary_category=category,
        secondary_categories=[],
        content_type=content_type,
        score=score,
        tags=["scientific-ml", "evidence"],
        model="test-analyzer",
        cost_usd=0.001,
    )
    summary = AI4SSummary(
        scientific_problem="识别目标科学问题。",
        ai_method="使用领域约束的机器学习模型。",
        main_result="获得可验证的主要结果。",
        innovation="连接数据驱动方法与物理机制。",
        scientific_significance="减少候选实验范围。",
        resources="https://example.com/code",
        model="test-summarizer",
        cost_usd=0.002,
    )
    storage.record_items([item])
    storage.save_analyzer_result(url, analyzer)
    storage.save_ai4s_summary(url, summary)
    analysis = storage.get_ai4s_analysis(url)
    assert analysis is not None
    return analysis


def _seed_reports(storage: Storage, *, daily: bool = True, weekly: bool = True) -> None:
    biology = _store_analysis(
        storage,
        "https://example.com/bio",
        title="Protein Design Study",
        category="biology",
    )
    materials = _store_analysis(
        storage,
        "https://example.com/materials",
        title="Materials Discovery Model",
        category="materials",
        content_type="model",
        score=9,
    )
    if daily:
        start, end = daily_period(date(2026, 9, 3))
        storage.create_report("daily", start, end, [materials, biology])
    if weekly:
        start, end = weekly_period(date(2026, 9, 6))
        storage.create_report(
            "weekly",
            start,
            end,
            [materials, biology],
            overview="本期多个领域关注科学模型的可靠性。",
            category_trends={
                "materials": "材料方向出现结合物理约束与数据模型的工作。",
            },
            watchlist=["科学模型的实验验证"],
            model="deepseek/deepseek-chat",
            cost_usd=0.001,
        )


def _render(tmp_path: Path, *, daily: bool = True, weekly: bool = True):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    if daily or weekly:
        _seed_reports(storage, daily=daily, weekly=weekly)
    output_dir = tmp_path / "site"
    result = render_ai4s_site(storage, output_dir=output_dir)
    storage.close()
    return result, (output_dir / "index.html").read_text(encoding="utf-8")


def test_render_with_daily_and_weekly_uses_report_layer_fields(tmp_path: Path):
    result, html = _render(tmp_path)

    assert result["daily_items"] == 2
    assert result["weekly_items"] == 2
    assert "AI4S Daily" in html
    assert "Daily" in html and "Weekly" in html
    for label in ("科学问题", "AI 方法", "主要结果", "创新点", "科研意义", "科研资源"):
        assert label in html
    assert "本期概览" in html
    assert "领域趋势" in html
    assert "持续关注" in html
    assert "代表工作" in html
    assert "技术方案" not in html
    assert "关键数据" not in html
    assert "为什么值得关注" not in html


def test_rendered_cards_use_report_category_and_content_type(tmp_path: Path):
    _, html = _render(tmp_path)

    assert 'data-category="biology"' in html
    assert 'data-category="materials"' in html
    assert ">Biology<" in html
    assert ">Materials<" in html
    assert ">Paper<" in html
    assert ">Model<" in html
    assert 'data-category-button="all"' in html
    assert 'data-category-button="earth"' in html


def test_render_with_daily_only_has_weekly_empty_state(tmp_path: Path):
    result, html = _render(tmp_path, weekly=False)

    assert result["daily_report_id"] is not None
    assert result["weekly_report_id"] is None
    assert "尚未生成 Weekly Report" in html


def test_render_with_weekly_only_has_daily_empty_state(tmp_path: Path):
    result, html = _render(tmp_path, daily=False)

    assert result["daily_report_id"] is None
    assert result["weekly_report_id"] is not None
    assert "尚未生成 Daily Report" in html


def test_render_without_reports_is_a_valid_empty_page(tmp_path: Path):
    result, html = _render(tmp_path, daily=False, weekly=False)

    assert result["daily_items"] == 0
    assert result["weekly_items"] == 0
    assert "尚未生成 Daily Report" in html
    assert "尚未生成 Weekly Report" in html


def test_persisted_empty_reports_render_friendly_states(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    daily_start, daily_end = daily_period(date(2026, 9, 3))
    weekly_start, weekly_end = weekly_period(date(2026, 9, 6))
    storage.create_report("daily", daily_start, daily_end, [])
    storage.create_report("weekly", weekly_start, weekly_end, [])
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "Daily Report 已生成，但没有符合条件的新内容" in html
    assert "Weekly Report 已生成，但没有符合条件的内容" in html


def test_html_escaping_viewport_and_mobile_layout(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    analysis = _store_analysis(
        storage,
        "https://example.com/escape",
        title="<script>alert('x')</script>",
        category="general",
    )
    start, end = daily_period(date(2026, 9, 3))
    storage.create_report("daily", start, end, [analysis])
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;" in html
    assert "<script>alert('x')</script>" not in html
    assert 'name="viewport"' in html
    assert "@media (max-width: 700px)" in html


def test_render_ai4s_cli_options():
    args = _parse_args([
        "render-ai4s",
        "--db",
        "data/ai4s_dev.db",
        "--output-dir",
        "site",
    ])

    assert args.db == "data/ai4s_dev.db"
    assert args.output_dir == "site"
