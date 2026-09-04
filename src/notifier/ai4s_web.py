from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader

from src.models import Report, ReportItem
from src.storage import Storage


CATEGORIES = (
    ("all", "All"),
    ("biology", "Biology"),
    ("medicine", "Medicine"),
    ("chemistry", "Chemistry"),
    ("materials", "Materials"),
    ("physics", "Physics"),
    ("earth", "Earth"),
    ("general", "General"),
)

CONTENT_TYPE_LABELS = {
    "paper": "Paper",
    "model": "Model",
    "dataset": "Dataset",
    "benchmark": "Benchmark",
    "tool": "Tool",
    "project": "Project",
    "research_news": "Research News",
}

_UNINFORMATIVE_SUMMARY_TEXTS = {
    "原文未说明",
    "原文未披露",
    "原文未披露明确量化结果",
    "原文未明确陈述科学意义",
    "未提供额外科研资源",
    "未说明",
    "未披露",
}

_DAILY_FIELDS = (
    ("科学问题", "scientific_problem", "problem"),
    ("AI 方法", "ai_method", "method"),
    ("主要结果", "main_result", "result"),
    ("创新点", "innovation", "innovation"),
    ("科研意义", "scientific_significance", "significance"),
)

_IMAGE_METADATA_KEYS = (
    "image",
    "image_url",
    "thumbnail",
    "thumbnail_url",
    "og:image",
    "og_image",
    "open_graph_image",
    "media_thumbnail",
    "media_content",
)

_IMAGE_CONTAINER_KEYS = ("open_graph", "opengraph", "og", "metadata")


def _normalize_summary_text(value: str) -> str:
    return " ".join(value.split()).rstrip("。.!！")


def is_informative_summary_text(value: str | None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return _normalize_summary_text(value) not in _UNINFORMATIVE_SUMMARY_TEXTS


def format_category_count(count: int) -> str:
    return "99+" if count >= 100 else str(count)


def _http_image_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def _image_url_from_value(value: object) -> str | None:
    direct = _http_image_url(value)
    if direct:
        return direct
    if isinstance(value, Mapping):
        lowered = {str(key).lower(): nested for key, nested in value.items()}
        for key in ("secure_url", "url", "src", "href"):
            candidate = _http_image_url(lowered.get(key))
            if candidate:
                return candidate
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            candidate = _image_url_from_value(nested)
            if candidate:
                return candidate
    return None


def source_image_url(raw: object) -> str | None:
    """Return an explicit HTTP(S) image URL from trusted metadata fields only."""
    if not isinstance(raw, Mapping):
        return None
    lowered = {str(key).lower(): value for key, value in raw.items()}
    for key in _IMAGE_METADATA_KEYS:
        candidate = _image_url_from_value(lowered.get(key))
        if candidate:
            return candidate
    for key in _IMAGE_CONTAINER_KEYS:
        nested = lowered.get(key)
        if isinstance(nested, Mapping):
            candidate = source_image_url(nested)
            if candidate:
                return candidate
    return None


def _category_counts(report: Report | None) -> dict[str, int]:
    counts = {category_id: 0 for category_id, _ in CATEGORIES}
    if report is None:
        return counts
    counts["all"] = len(report.items)
    for item in report.items:
        if item.category in counts:
            counts[item.category] += 1
    return counts


def build_report_overview(report: Report | None) -> dict[str, object]:
    """Build deterministic presentation metrics from persisted report items."""
    category_counts = _category_counts(report)
    content_type_counts = {content_type: 0 for content_type in CONTENT_TYPE_LABELS}
    sources: set[str] = set()
    if report is not None:
        for report_item in report.items:
            analyzer = report_item.analysis.analyzer
            if analyzer.content_type in content_type_counts:
                content_type_counts[analyzer.content_type] += 1
            sources.add(report_item.analysis.item.source)

    ranked_categories = [category_id for category_id, _ in CATEGORIES if category_id != "all"]
    top_category = max(ranked_categories, key=category_counts.get) if report and report.items else None
    return {
        "total": category_counts["all"],
        "category_counts": category_counts,
        "content_type_counts": content_type_counts,
        "source_count": len(sources),
        "covered_categories": sum(category_counts[key] > 0 for key in ranked_categories),
        "top_category": top_category,
        "max_category_count": max((category_counts[key] for key in ranked_categories), default=0),
        "max_content_type_count": max(content_type_counts.values(), default=0),
    }


def _daily_card_view(report_item: ReportItem) -> dict[str, object]:
    summary = report_item.analysis.summary
    sections = []
    if summary is not None:
        sections = [
            {"label": label, "text": getattr(summary, field_name), "kind": kind}
            for label, field_name, kind in _DAILY_FIELDS
            if is_informative_summary_text(getattr(summary, field_name))
        ]
    low_information = len(sections) < 2
    resources = (
        summary.resources
        if summary is not None and is_informative_summary_text(summary.resources)
        else None
    )
    return {
        "report_item": report_item,
        "sections": [] if low_information else sections,
        "resources": resources,
        "low_information": low_information,
        "image_url": source_image_url(report_item.analysis.item.raw),
    }


def _weekly_card_view(report_item: ReportItem) -> dict[str, object]:
    summary = report_item.analysis.summary
    sections: list[dict[str, str]] = []
    used_texts: set[str] = set()

    def add_first(label: str, *values: str | None) -> None:
        for value in values:
            if not is_informative_summary_text(value):
                continue
            normalized = _normalize_summary_text(value)
            if normalized in used_texts:
                continue
            sections.append({"label": label, "text": value})
            used_texts.add(normalized)
            return

    if summary is not None:
        add_first("方法亮点", summary.ai_method, summary.innovation)
        add_first("关键结果", summary.main_result)
        add_first(
            "值得关注",
            summary.scientific_significance,
            summary.innovation,
            summary.scientific_problem,
        )

    return {
        "report_item": report_item,
        "sections": sections,
        "low_information": not sections,
        "image_url": source_image_url(report_item.analysis.item.raw),
    }


def render_ai4s_site(
    storage: Storage,
    *,
    output_dir: Path = Path("site"),
    templates_dir: Path = Path("templates"),
) -> dict[str, object]:
    """Render persisted AI4S reports without repeating report selection logic."""
    daily = storage.get_latest_daily_report()
    weekly = storage.get_latest_weekly_report()
    daily_overview = build_report_overview(daily)
    weekly_overview = build_report_overview(weekly)
    daily_category_counts = daily_overview["category_counts"]
    weekly_category_counts = weekly_overview["category_counts"]
    source_count = len(
        {
            item.analysis.item.source
            for report in (daily, weekly)
            if report is not None
            for item in report.items
        }
    )

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    template = env.get_template("ai4s_dashboard.html.j2")
    html = template.render(
        daily=daily,
        weekly=weekly,
        daily_cards=[_daily_card_view(item) for item in daily.items] if daily else [],
        weekly_cards=[_weekly_card_view(item) for item in weekly.items] if weekly else [],
        categories=CATEGORIES,
        category_labels=dict(CATEGORIES),
        daily_category_counts=daily_category_counts,
        weekly_category_counts=weekly_category_counts,
        daily_overview=daily_overview,
        weekly_overview=weekly_overview,
        source_count=source_count,
        format_category_count=format_category_count,
        content_type_labels=CONTENT_TYPE_LABELS,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(html, encoding="utf-8")
    return {
        "daily_report_id": daily.id if daily else None,
        "daily_items": len(daily.items) if daily else 0,
        "weekly_report_id": weekly.id if weekly else None,
        "weekly_items": len(weekly.items) if weekly else 0,
        "output": str(output_path),
    }
