from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader

from src.models import Report, ReportItem
from src.source_info import (
    SOURCE_FAMILY_LABELS,
    SOURCE_FAMILY_ORDER,
    SourceInfo,
    source_info,
    source_info_from_config,
)
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

_DAILY_OVERVIEW_FIELDS = (
    ("问题", "scientific_problem"),
    ("方法", "ai_method"),
    ("结果", "main_result"),
)

_DAILY_LEGACY_FIELDS = (
    ("创新", "innovation"),
    ("意义", "scientific_significance"),
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
    source_counts: dict[str, dict[str, object]] = {}
    source_family_counts: Counter[str] = Counter()
    if report is not None:
        for report_item in report.items:
            analyzer = report_item.analysis.analyzer
            if analyzer.content_type in content_type_counts:
                content_type_counts[analyzer.content_type] += 1
            info = source_info(report_item.analysis.item.source)
            source = source_counts.setdefault(
                info.key,
                {
                    "key": info.key,
                    "name": info.display_name,
                    "family": info.family,
                    "family_label": SOURCE_FAMILY_LABELS[info.family],
                    "count": 0,
                },
            )
            source["count"] = int(source["count"]) + 1
            source_family_counts[info.family] += 1

    ranked_categories = [category_id for category_id, _ in CATEGORIES if category_id != "all"]
    top_category = max(ranked_categories, key=category_counts.get) if report and report.items else None
    source_distribution = sorted(
        source_counts.values(),
        key=lambda source: (-int(source["count"]), str(source["name"])),
    )
    return {
        "total": category_counts["all"],
        "category_counts": category_counts,
        "content_type_counts": content_type_counts,
        "source_count": len(source_distribution),
        "source_family_count": len(source_family_counts),
        "source_distribution": source_distribution,
        "source_family_counts": dict(source_family_counts),
        "covered_categories": sum(category_counts[key] > 0 for key in ranked_categories),
        "top_category": top_category,
        "max_category_count": max((category_counts[key] for key in ranked_categories), default=0),
        "max_content_type_count": max(content_type_counts.values(), default=0),
    }


def build_source_coverage(
    sources: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Summarize configured sources for the static presentation layer."""
    active_sources = [source for source in sources if source.get("enabled", True) is not False]
    groups_by_id = {
        group_id: {"id": group_id, "label": SOURCE_FAMILY_LABELS[group_id], "count": 0, "providers": []}
        for group_id in SOURCE_FAMILY_ORDER
        if group_id != "other"
    }
    for source in active_sources:
        info = source_info_from_config(dict(source))
        group_id = str(source.get("group") or info.family)
        group = groups_by_id.get(group_id)
        if group is None:
            continue
        group["count"] = int(group["count"]) + 1
        provider = str(source.get("provider") or info.display_name).strip()
        providers = group["providers"]
        if provider and provider not in providers:
            providers.append(provider)

    groups = [
        groups_by_id[group_id]
        for group_id in SOURCE_FAMILY_ORDER
        if group_id in groups_by_id and groups_by_id[group_id]["count"]
    ]
    return {
        "active_count": len(active_sources),
        "family_count": len(groups),
        "groups": groups,
    }


def build_source_distribution(
    daily_overview: dict[str, object], weekly_overview: dict[str, object]
) -> list[dict[str, object]]:
    sources: dict[str, dict[str, object]] = {}
    for view_name, overview in (("daily", daily_overview), ("weekly", weekly_overview)):
        for source in overview["source_distribution"]:
            row = sources.setdefault(
                source["key"],
                {
                    "key": source["key"],
                    "name": source["name"],
                    "family": source["family"],
                    "daily_count": 0,
                    "weekly_count": 0,
                },
            )
            row[f"{view_name}_count"] = source["count"]
    return sorted(
        sources.values(),
        key=lambda source: (
            -max(int(source["daily_count"]), int(source["weekly_count"])),
            str(source["name"]),
        ),
    )


def build_source_filters(
    sources: Sequence[Mapping[str, object]],
    daily_overview: dict[str, object],
    weekly_overview: dict[str, object],
) -> list[dict[str, object]]:
    active_infos = {
        info.key: info
        for source in sources
        if source.get("enabled", True) is not False
        for info in (source_info_from_config(dict(source)),)
    }
    for overview in (daily_overview, weekly_overview):
        for source in overview["source_distribution"]:
            if source["key"] not in active_infos:
                active_infos[source["key"]] = SourceInfo(
                    key=str(source["key"]),
                    display_name=str(source["name"]),
                    family=str(source["family"]),
                    type_label="Source",
                )

    def count(overview: dict[str, object], kind: str, value: str) -> int:
        if kind == "all":
            return int(overview["total"])
        if kind == "family":
            return int(overview["source_family_counts"].get(value, 0))
        return sum(
            int(source["count"])
            for source in overview["source_distribution"]
            if source["key"] == value
        )

    filters = [{"kind": "all", "value": "all", "label": "All Sources"}]
    for family in SOURCE_FAMILY_ORDER:
        family_infos = [info for info in active_infos.values() if info.family == family]
        if not family_infos:
            continue
        if family == "research_labs":
            filters.append(
                {"kind": "family", "value": family, "label": SOURCE_FAMILY_LABELS[family]}
            )
            continue
        for info in family_infos:
            filters.append({"kind": "name", "value": info.key, "label": info.display_name})

    for source_filter in filters:
        source_filter["daily_count"] = count(
            daily_overview, str(source_filter["kind"]), str(source_filter["value"])
        )
        source_filter["weekly_count"] = count(
            weekly_overview, str(source_filter["kind"]), str(source_filter["value"])
        )
    return filters


def _daily_card_view(report_item: ReportItem) -> dict[str, object]:
    summary = report_item.analysis.summary
    overview = []
    assessment = None
    legacy_insights = []
    if summary is not None:
        overview = [
            {"label": label, "text": getattr(summary, field_name)}
            for label, field_name in _DAILY_OVERVIEW_FIELDS
            if is_informative_summary_text(getattr(summary, field_name))
        ]
        if is_informative_summary_text(summary.assessment):
            assessment = summary.assessment
        elif summary.assessment is None:
            legacy_insights = [
                {"label": label, "text": getattr(summary, field_name)}
                for label, field_name in _DAILY_LEGACY_FIELDS
                if is_informative_summary_text(getattr(summary, field_name))
            ]
    low_information = not overview and not assessment and not legacy_insights
    resources = (
        summary.resources
        if summary is not None and is_informative_summary_text(summary.resources)
        else None
    )
    return {
        "report_item": report_item,
        "source": source_info(report_item.analysis.item.source),
        "overview": overview,
        "assessment": assessment,
        "legacy_insights": legacy_insights,
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
            summary.assessment,
            summary.scientific_significance,
            summary.innovation,
            summary.scientific_problem,
        )

    return {
        "report_item": report_item,
        "source": source_info(report_item.analysis.item.source),
        "sections": sections,
        "low_information": not sections,
        "image_url": source_image_url(report_item.analysis.item.raw),
    }


def render_ai4s_site(
    storage: Storage,
    *,
    sources: Sequence[Mapping[str, object]] = (),
    output_dir: Path = Path("site"),
    templates_dir: Path = Path("templates"),
) -> dict[str, object]:
    """Render persisted AI4S reports without repeating report selection logic."""
    daily = storage.get_latest_daily_report()
    weekly = storage.get_latest_weekly_report()
    daily_overview = build_report_overview(daily)
    weekly_overview = build_report_overview(weekly)
    source_coverage = build_source_coverage(sources)
    source_distribution = build_source_distribution(daily_overview, weekly_overview)
    source_filters = build_source_filters(sources, daily_overview, weekly_overview)
    daily_category_counts = daily_overview["category_counts"]
    weekly_category_counts = weekly_overview["category_counts"]
    source_count = len(
        {
            source_info(item.analysis.item.source).key
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
        source_coverage=source_coverage,
        source_distribution=source_distribution,
        source_filters=source_filters,
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
        "active_sources": source_coverage["active_count"],
        "source_families": source_coverage["family_count"],
        "daily_unique_sources": daily_overview["source_count"],
        "output": str(output_path),
    }
