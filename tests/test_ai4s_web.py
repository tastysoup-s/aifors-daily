from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.ai4s_daily import daily_period
from src.ai4s_weekly import weekly_period
from src.main import _parse_args
from src.models import AI4SAnalysis, AI4SSummary, AnalyzerResult, Item
from src.notifier.ai4s_web import (
    build_report_overview,
    build_source_coverage,
    format_category_count,
    is_informative_summary_text,
    render_ai4s_site,
    source_image_url,
)
from src.storage import Storage


def _store_analysis(
    storage: Storage,
    url: str,
    *,
    title: str,
    category: str,
    content_type: str = "paper",
    score: int = 8,
    summary_values: dict[str, str] | None = None,
    raw: dict | None = None,
) -> AI4SAnalysis:
    item = Item(
        url=url,
        title=title,
        content="raw content",
        published_at=datetime(2026, 9, 3, 8, tzinfo=timezone.utc),
        source="test-source",
        raw=raw or {},
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
    values = {
        "scientific_problem": "识别目标科学问题。",
        "ai_method": "使用领域约束的机器学习模型。",
        "main_result": "获得可验证的主要结果。",
        "innovation": "连接数据驱动方法与物理机制。",
        "scientific_significance": "减少候选实验范围。",
        "resources": "https://example.com/code",
    }
    values.update(summary_values or {})
    summary = AI4SSummary(
        **values,
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


def _card_for(html: str, title: str) -> str:
    title_position = html.index(title)
    start = html.rfind("<article", 0, title_position)
    end = html.index("</article>", title_position) + len("</article>")
    return html[start:end]


def _category_button(html: str, category: str) -> str:
    marker_position = html.index(f'data-category-button="{category}"')
    start = html.rfind("<button", 0, marker_position)
    end = html.index("</button>", marker_position) + len("</button>")
    return html[start:end]


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
    assert 'class="badges"' in html
    assert 'class="tags"' in html
    assert 'class="count-badge" data-category-count' in html
    assert "flex-wrap: wrap" in html
    assert "overflow-wrap: anywhere" in html


def test_report_overview_uses_persisted_report_data(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    biology = _store_analysis(
        storage,
        "https://example.com/overview-bio",
        title="Biology Paper",
        category="biology",
        content_type="paper",
    )
    materials = _store_analysis(
        storage,
        "https://example.com/overview-materials",
        title="Materials Model",
        category="materials",
        content_type="model",
    )
    start, end = daily_period(date(2026, 9, 3))
    report, _ = storage.create_report("daily", start, end, [biology, materials])

    overview = build_report_overview(report)
    storage.close()

    assert overview["total"] == 2
    assert overview["covered_categories"] == 2
    assert overview["source_count"] == 1
    assert overview["category_counts"]["biology"] == 1
    assert overview["content_type_counts"]["paper"] == 1
    assert overview["content_type_counts"]["model"] == 1


def test_source_coverage_uses_configured_groups_and_deduplicates_providers():
    coverage = build_source_coverage([
        {"name": "arxiv-ai", "type": "arxiv", "group": "papers", "provider": "arXiv"},
        {"name": "arxiv-bio", "type": "arxiv", "group": "papers", "provider": "arXiv"},
        {"name": "biorxiv", "type": "rss", "group": "preprints", "provider": "bioRxiv"},
        {"name": "github", "type": "github", "group": "code", "provider": "GitHub"},
        {"name": "deepmind", "type": "rss", "group": "research_labs", "provider": "Google DeepMind"},
        {"name": "hn", "type": "hackernews", "group": "community", "provider": "Hacker News"},
    ])

    assert coverage["active_count"] == 6
    assert [group["label"] for group in coverage["groups"]] == [
        "Papers", "Preprints", "Open Source", "Research Labs", "Community"
    ]
    assert coverage["groups"][0]["providers"] == ["arXiv"]


def test_rendered_source_coverage_comes_from_config(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    output_dir = tmp_path / "site"
    render_ai4s_site(
        storage,
        sources=[
            {"name": "arxiv-ai", "type": "arxiv", "group": "papers", "provider": "arXiv"},
            {"name": "biorxiv", "type": "rss", "group": "preprints", "provider": "bioRxiv"},
            {"name": "github", "type": "github", "group": "code", "provider": "GitHub"},
            {"name": "apple", "type": "rss", "group": "research_labs", "provider": "Apple Machine Learning Research"},
            {"name": "hn", "type": "hackernews", "group": "community", "provider": "Hacker News"},
        ],
        output_dir=output_dir,
    )
    storage.close()

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "Source Coverage" in html
    assert "5 active sources" in html
    for label in ("Papers", "Preprints", "Open Source", "Research Labs", "Community"):
        assert f">{label}<" in html
    assert "Apple Machine Learning Research" in html


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ({"image_url": "https://images.example.com/paper.png"}, "https://images.example.com/paper.png"),
        ({"media_thumbnail": [{"url": "https://images.example.com/thumb.jpg"}]}, "https://images.example.com/thumb.jpg"),
        ({"open_graph": {"og:image": "https://images.example.com/og.webp"}}, "https://images.example.com/og.webp"),
        ({"image": "javascript:alert(1)"}, None),
        ({"url": "https://example.com/article"}, None),
    ),
)
def test_source_image_url_only_accepts_explicit_http_image_metadata(raw, expected):
    assert source_image_url(raw) == expected


def test_rendered_visual_uses_source_image_or_category_fallback(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    with_image = _store_analysis(
        storage,
        "https://example.com/with-image",
        title="Paper With Image",
        category="biology",
        raw={"media_content": [{"url": "https://images.example.com/source.jpg"}]},
    )
    fallback = _store_analysis(
        storage,
        "https://example.com/fallback",
        title="Paper Without Image",
        category="physics",
    )
    earth_image = _store_analysis(
        storage,
        "https://example.com/earth-image",
        title="Earth Paper With Image",
        category="earth",
        raw={"image_url": "https://images.example.com/earth-wide.jpg"},
    )
    start, end = daily_period(date(2026, 9, 3))
    storage.create_report("daily", start, end, [with_image, fallback, earth_image])
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    image_card = _card_for(html, "Paper With Image")
    fallback_card = _card_for(html, "Paper Without Image")
    earth_card = _card_for(html, "Earth Paper With Image")
    assert 'data-visual-kind="source-image"' in image_card
    assert 'src="https://images.example.com/source.jpg"' in image_card
    assert 'loading="lazy"' in image_card
    assert 'referrerpolicy="no-referrer"' in image_card
    assert 'data-visual-kind="category-fallback"' in fallback_card
    assert 'href="#icon-physics"' in fallback_card
    assert 'data-category="earth"' in earth_card
    assert 'data-earth-visual' in earth_card
    assert 'data-source-image' in earth_card
    assert "visualFit < .62" in html
    assert "image.naturalWidth < 420" in html


def test_dashboard_navigation_pipeline_and_responsive_contract(tmp_path: Path):
    _, html = _render(tmp_path)

    header = html.split("</header>", 1)[0]
    footer = html.split('<footer class="site-footer">', 1)[1]
    assert "Scientific Intelligence Map" in html
    assert 'href="#daily"' in header
    assert 'href="#weekly"' in header
    assert 'href="#categories"' not in header
    assert 'href="#about"' not in header
    assert 'class="footer-links"' in footer
    assert 'href="#categories"' in footer
    assert 'href="#about"' in footer
    assert 'id="categories"' in html
    assert 'class="section-heading overview-heading"' in html
    assert html.count('class="dashboard-panel-head"') == 2
    assert html.count('class="dashboard-panel-content') == 2
    assert "data-dashboard-category" in html
    assert "data-type-segment" in html
    assert 'id="about"' in html
    for step in ("Sources", "Fetcher", "AI4S Analyzer", "High-value Filter", "Summarizer", "Daily / Weekly", "GitHub Pages"):
        assert step in html
    assert "@media (max-width: 1024px)" in html
    assert "@media (max-width: 768px)" in html
    assert "@media (max-width: 390px)" in html
    assert "@media (prefers-reduced-motion: reduce)" in html


def test_category_counts_include_all_categories_and_zero_values(tmp_path: Path):
    _, html = _render(tmp_path)

    all_button = _category_button(html, "all")
    biology_button = _category_button(html, "biology")
    materials_button = _category_button(html, "materials")
    medicine_button = _category_button(html, "medicine")
    assert 'data-count-daily="2"' in all_button
    assert 'data-count-weekly="2"' in all_button
    assert ">2</span>" in all_button
    assert 'data-count-daily="1"' in biology_button
    assert 'data-count-daily="1"' in materials_button
    assert 'data-count-daily="0"' in medicine_button
    assert "is-zero" in medicine_button
    assert ">0</span>" in medicine_button


def test_daily_and_weekly_category_counts_use_their_own_report_items(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    biology = _store_analysis(
        storage,
        "https://example.com/count-bio",
        title="Biology Count",
        category="biology",
    )
    materials = _store_analysis(
        storage,
        "https://example.com/count-materials",
        title="Materials Count",
        category="materials",
    )
    daily_start, daily_end = daily_period(date(2026, 9, 3))
    weekly_start, weekly_end = weekly_period(date(2026, 9, 6))
    storage.create_report("daily", daily_start, daily_end, [biology, materials])
    storage.create_report("weekly", weekly_start, weekly_end, [biology])
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert 'data-count-daily="2"' in _category_button(html, "all")
    assert 'data-count-weekly="1"' in _category_button(html, "all")
    assert 'data-count-daily="1"' in _category_button(html, "materials")
    assert 'data-count-weekly="0"' in _category_button(html, "materials")


@pytest.mark.parametrize(
    ("count", "label"),
    ((0, "0"), (99, "99"), (100, "99+"), (150, "99+")),
)
def test_category_count_formatting(count: int, label: str):
    assert format_category_count(count) == label


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        "   ",
        "原文未说明。",
        "原文未披露明确量化结果。",
        "原文未明确陈述科学意义。",
        "未提供额外科研资源。",
        "未说明",
        "未披露。",
    ),
)
def test_uninformative_summary_text_is_rejected(value: str | None):
    assert is_informative_summary_text(value) is False


def test_real_scientific_summary_text_is_informative():
    assert is_informative_summary_text("模型将病理切片推理速度提高了三倍。") is True


def test_daily_hides_uninformative_fields_without_empty_headings(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    analysis = _store_analysis(
        storage,
        "https://example.com/daily-informative",
        title="Daily Informative Card",
        category="chemistry",
        summary_values={
            "main_result": "原文未披露明确量化结果。",
            "resources": "未提供额外科研资源。",
        },
    )
    start, end = daily_period(date(2026, 9, 3))
    storage.create_report("daily", start, end, [analysis])
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    card = _card_for(
        (output_dir / "index.html").read_text(encoding="utf-8"),
        "Daily Informative Card",
    )
    for label in ("科学问题", "AI 方法", "创新点", "科研意义"):
        assert f"<h4>{label}</h4>" in card
    assert "<h4>主要结果</h4>" not in card
    assert "<h4>科研资源</h4>" not in card
    assert "原文未披露明确量化结果" not in card


def test_daily_low_information_uses_one_fallback_and_keeps_resources(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    analysis = _store_analysis(
        storage,
        "https://example.com/daily-limited",
        title="Daily Limited Card",
        category="general",
        summary_values={
            "scientific_problem": "仅有一个有效科学问题。",
            "ai_method": "原文未说明。",
            "main_result": "原文未披露明确量化结果。",
            "innovation": "未说明。",
            "scientific_significance": "未披露。",
            "resources": "https://example.com/paper",
        },
    )
    start, end = daily_period(date(2026, 9, 3))
    storage.create_report("daily", start, end, [analysis])
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    card = _card_for(
        (output_dir / "index.html").read_text(encoding="utf-8"),
        "Daily Limited Card",
    )
    assert "信息有限" in card
    assert "暂无足够信息生成完整科研解读" in card
    assert "<h4>科学问题</h4>" not in card
    assert "<h4>科研资源</h4>" in card
    assert "https://example.com/paper" in card


def test_weekly_cards_map_fields_hide_no_result_and_suppress_duplicates(tmp_path: Path):
    storage = Storage(tmp_path / "reports.db")
    storage.init()
    complete = _store_analysis(
        storage,
        "https://example.com/weekly-complete",
        title="Weekly Complete Card",
        category="biology",
    )
    no_result = _store_analysis(
        storage,
        "https://example.com/weekly-no-result",
        title="Weekly No Result Card",
        category="medicine",
        summary_values={"main_result": "原文未披露明确量化结果。"},
    )
    duplicate = _store_analysis(
        storage,
        "https://example.com/weekly-duplicate",
        title="Weekly Duplicate Card",
        category="physics",
        summary_values={
            "scientific_problem": "解释独立的科学问题。",
            "ai_method": "相同的方法亮点。",
            "main_result": "未披露。",
            "innovation": "相同的方法亮点",
            "scientific_significance": "未说明。",
        },
    )
    limited = _store_analysis(
        storage,
        "https://example.com/weekly-limited",
        title="Weekly Limited Card",
        category="earth",
        summary_values={
            "scientific_problem": "原文未说明。",
            "ai_method": "原文未说明。",
            "main_result": "原文未披露明确量化结果。",
            "innovation": "未说明。",
            "scientific_significance": "未披露。",
            "resources": "未提供额外科研资源。",
        },
    )
    start, end = weekly_period(date(2026, 9, 6))
    storage.create_report(
        "weekly",
        start,
        end,
        [complete, no_result, duplicate, limited],
        overview="Weekly overview",
    )
    output_dir = tmp_path / "site"
    render_ai4s_site(storage, output_dir=output_dir)
    storage.close()

    html = (output_dir / "index.html").read_text(encoding="utf-8")
    complete_card = _card_for(html, "Weekly Complete Card")
    assert "<h4>方法亮点</h4>" in complete_card
    assert "<h4>关键结果</h4>" in complete_card
    assert "<h4>值得关注</h4>" in complete_card

    no_result_card = _card_for(html, "Weekly No Result Card")
    assert "<h4>方法亮点</h4>" in no_result_card
    assert "<h4>关键结果</h4>" not in no_result_card
    assert "原文未披露明确量化结果" not in no_result_card
    assert "<h4>值得关注</h4>" in no_result_card

    duplicate_card = _card_for(html, "Weekly Duplicate Card")
    assert duplicate_card.count("相同的方法亮点") == 1
    assert "<h4>值得关注</h4>" in duplicate_card
    assert "解释独立的科学问题" in duplicate_card

    limited_card = _card_for(html, "Weekly Limited Card")
    assert "信息有限" in limited_card
    assert "建议查看原文了解完整方法与结果" in limited_card
    assert "<h4>方法亮点</h4>" not in limited_card
    assert "<h4>关键结果</h4>" not in limited_card
    assert "<h4>值得关注</h4>" not in limited_card


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
    assert "flex-shrink: 0" in html
    assert "count >= 100 ? '99+'" in html


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
