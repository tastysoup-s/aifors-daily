import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from src.config import Config
from src.llm import LLMError, check_api_keys, complete_json
from src.models import AI4S_CATEGORY_IDS, AI4SAnalysis, Report
from src.prompts import load_prompt, render
from src.storage import Storage


logger = logging.getLogger(__name__)

WEEKLY_CANDIDATE_LIMIT = 30
WEEKLY_REPRESENTATIVE_LIMIT = 10
WEEKLY_REPRESENTATIVE_MIN = 5


def weekly_period(report_date: date) -> tuple[datetime, datetime]:
    if report_date.weekday() == 2:  # Wednesday: Monday through Wednesday
        start_date = report_date - timedelta(days=2)
    elif report_date.weekday() == 6:  # Sunday: Thursday through Sunday
        start_date = report_date - timedelta(days=3)
    else:
        raise ValueError("weekly report date must be a Wednesday or Sunday")
    return (
        datetime.combine(start_date, time.min, tzinfo=timezone.utc),
        datetime.combine(report_date, time.max, tzinfo=timezone.utc),
    )


def latest_weekly_report_date(current_date: date) -> date:
    for days_ago in range(7):
        candidate = current_date - timedelta(days=days_ago)
        if candidate.weekday() in (2, 6):
            return candidate
    raise AssertionError("a Wednesday or Sunday must exist in every seven-day span")


def select_representative_works(
    candidates: list[AI4SAnalysis],
) -> list[AI4SAnalysis]:
    selected_urls: set[str] = set()

    # Preserve at least one representative from every present category.
    seen_categories: set[str] = set()
    for analysis in candidates:
        category = analysis.analyzer.primary_category
        if category not in seen_categories:
            selected_urls.add(analysis.item.url)
            seen_categories.add(category)

    # Then add a second work per category while the report has room.
    per_category: dict[str, int] = defaultdict(int)
    for analysis in candidates:
        category = analysis.analyzer.primary_category
        per_category[category] += 1
        if per_category[category] == 2 and len(selected_urls) < WEEKLY_REPRESENTATIVE_LIMIT:
            selected_urls.add(analysis.item.url)

    for analysis in candidates:
        if len(selected_urls) >= min(WEEKLY_REPRESENTATIVE_MIN, len(candidates)):
            break
        selected_urls.add(analysis.item.url)

    return [
        analysis for analysis in candidates if analysis.item.url in selected_urls
    ][:WEEKLY_REPRESENTATIVE_LIMIT]


def _render_weekly_prompt(
    candidates: list[AI4SAnalysis], period_start: datetime, period_end: datetime
) -> str:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for analysis in candidates:
        summary = analysis.summary
        assert summary is not None
        grouped[analysis.analyzer.primary_category].append({
            "title": analysis.item.title,
            "category": analysis.analyzer.primary_category,
            "content_type": analysis.analyzer.content_type,
            "score": analysis.analyzer.score,
            "tags": analysis.analyzer.tags,
            "scientific_problem": summary.scientific_problem,
            "ai_method": summary.ai_method,
            "main_result": summary.main_result,
            "innovation": summary.innovation,
            "scientific_significance": summary.scientific_significance,
        })
    return render(load_prompt("weekly_ai4s"), {
        "period_start": period_start.date().isoformat(),
        "period_end": period_end.date().isoformat(),
        "grouped_summaries": json.dumps(grouped, ensure_ascii=False, indent=2),
    })


def _validate_synthesis(
    data: dict, candidate_categories: set[str]
) -> tuple[str, dict[str, str], list[str]]:
    overview = data.get("overview")
    if not isinstance(overview, str) or not overview.strip():
        raise LLMError("weekly synthesis requires a non-empty overview")

    category_trends = data.get("category_trends")
    if not isinstance(category_trends, dict):
        raise LLMError("weekly synthesis category_trends must be an object")
    if any(not isinstance(category, str) for category in category_trends):
        raise LLMError("weekly synthesis contains invalid category keys")
    invalid_categories = set(category_trends) - AI4S_CATEGORY_IDS
    unsupported_categories = set(category_trends) - candidate_categories
    if invalid_categories:
        raise LLMError(
            f"weekly synthesis contains invalid category keys: {sorted(invalid_categories)}"
        )
    if unsupported_categories:
        raise LLMError(
            "weekly synthesis contains categories without candidates: "
            f"{sorted(unsupported_categories)}"
        )
    if any(not isinstance(value, str) or not value.strip() for value in category_trends.values()):
        raise LLMError("weekly synthesis category trends must be non-empty strings")

    watchlist = data.get("watchlist")
    if not isinstance(watchlist, list) or any(
        not isinstance(value, str) or not value.strip() for value in watchlist
    ):
        raise LLMError("weekly synthesis watchlist must be a list of non-empty strings")
    return overview, category_trends, watchlist


async def generate_weekly_report(
    storage: Storage,
    cfg: Config,
    report_date: date,
) -> dict[str, object]:
    period_start, period_end = weekly_period(report_date)
    candidates = storage.get_report_candidates(
        period_start,
        period_end,
        min_score=cfg.score_threshold,
        limit=WEEKLY_CANDIDATE_LIMIT,
    )
    existing = storage.get_report_by_period("weekly", period_start, period_end)
    if existing is not None:
        return _metrics(existing, len(candidates), created=False, llm_calls=0, cost_usd=0.0)

    representatives = select_representative_works(candidates)
    if not candidates:
        report, created = storage.create_report(
            "weekly", period_start, period_end, representatives
        )
        return _metrics(report, 0, created=created, llm_calls=0, cost_usd=0.0)

    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    check_api_keys(cfg.models)
    data, cost_usd = await complete_json(
        model=cfg.models.summarizer,
        prompt=_render_weekly_prompt(candidates, period_start, period_end),
        max_tokens=1600,
    )
    overview, category_trends, watchlist = _validate_synthesis(
        data,
        {analysis.analyzer.primary_category for analysis in candidates},
    )
    report, created = storage.create_report(
        "weekly",
        period_start,
        period_end,
        representatives,
        overview=overview,
        category_trends=category_trends,
        watchlist=watchlist,
        model=cfg.models.summarizer,
        cost_usd=cost_usd,
    )
    result = _metrics(
        report,
        len(candidates),
        created=created,
        llm_calls=1,
        cost_usd=cost_usd,
    )
    logger.info(
        "weekly report: period=%s..%s candidates=%d representatives=%d "
        "report_id=%d cost=$%.6f",
        period_start.date(),
        period_end.date(),
        len(candidates),
        len(representatives),
        report.id,
        cost_usd,
    )
    return result


def _metrics(
    report: Report,
    candidates: int,
    *,
    created: bool,
    llm_calls: int,
    cost_usd: float,
) -> dict[str, object]:
    return {
        "period": (
            f"{report.period_start.date().isoformat()}.."
            f"{report.period_end.date().isoformat()}"
        ),
        "candidates": candidates,
        "representatives": len(report.items),
        "categories": sorted({item.category for item in report.items}),
        "report_id": report.id,
        "created": created,
        "llm_calls": llm_calls,
        "cost_usd": cost_usd,
    }
