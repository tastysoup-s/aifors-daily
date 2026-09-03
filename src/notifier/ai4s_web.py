from datetime import datetime, timezone
from pathlib import Path

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
    ("科学问题", "scientific_problem"),
    ("AI 方法", "ai_method"),
    ("主要结果", "main_result"),
    ("创新点", "innovation"),
    ("科研意义", "scientific_significance"),
)


def _normalize_summary_text(value: str) -> str:
    return " ".join(value.split()).rstrip("。.!！")


def is_informative_summary_text(value: str | None) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    return _normalize_summary_text(value) not in _UNINFORMATIVE_SUMMARY_TEXTS


def format_category_count(count: int) -> str:
    return "99+" if count >= 100 else str(count)


def _category_counts(report: Report | None) -> dict[str, int]:
    counts = {category_id: 0 for category_id, _ in CATEGORIES}
    if report is None:
        return counts
    counts["all"] = len(report.items)
    for item in report.items:
        if item.category in counts:
            counts[item.category] += 1
    return counts


def _daily_card_view(report_item: ReportItem) -> dict[str, object]:
    summary = report_item.analysis.summary
    sections = []
    if summary is not None:
        sections = [
            {"label": label, "text": getattr(summary, field_name)}
            for label, field_name in _DAILY_FIELDS
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
    daily_category_counts = _category_counts(daily)
    weekly_category_counts = _category_counts(weekly)

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    template = env.get_template("ai4s_index.html.j2")
    html = template.render(
        daily=daily,
        weekly=weekly,
        daily_cards=[_daily_card_view(item) for item in daily.items] if daily else [],
        weekly_cards=[_weekly_card_view(item) for item in weekly.items] if weekly else [],
        categories=CATEGORIES,
        category_labels=dict(CATEGORIES),
        daily_category_counts=daily_category_counts,
        weekly_category_counts=weekly_category_counts,
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
