import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from src.ai4s_daily import select_daily_candidates
from src.config import Config
from src.information_sufficiency import (
    information_score,
    insufficient_information_reason,
    recommendation_sort_key,
    recommendation_quality_tier,
)
from src.llm import LLMError, check_api_keys, complete_json
from src.models import AI4S_CATEGORY_IDS, AI4SAnalysis, Report
from src.prompts import load_prompt, render
from src.source_info import source_info
from src.storage import Storage


logger = logging.getLogger(__name__)

WEEKLY_SYNTHESIS_CANDIDATE_LIMIT = 30
WEEKLY_REPRESENTATIVE_LIMIT = 6
WEEKLY_MIN_CATEGORIES = 6


def weekly_period(report_date: date) -> tuple[datetime, datetime]:
    if report_date.weekday() not in (2, 6):
        raise ValueError("weekly report date must be a Wednesday or Sunday")
    start_date = report_date - timedelta(days=6)
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
    candidate_count = len(candidates)
    qualified = []
    for analysis in candidates:
        reason = insufficient_information_reason(analysis)
        if reason is None and analysis.analyzer.is_ai4s:
            qualified.append(analysis)
        else:
            logger.info(
                "weekly filtered sparse: %s information_score=%d reason=%s",
                analysis.item.title, information_score(analysis), reason,
            )
    pool = list({a.item.url: a for a in qualified}.values())
    selected: list[AI4SAnalysis] = []
    categories: set[str] = set()
    families: Counter[str] = Counter()
    # A domain's best research paper is preferred; ecosystem signals fill gaps.
    while pool and len(selected) < WEEKLY_REPRESENTATIVE_LIMIT:
        uncovered = [a for a in pool if a.analyzer.primary_category not in categories]
        eligible = uncovered if uncovered and len(categories) < WEEKLY_MIN_CATEGORIES else pool
        chosen = max(eligible, key=lambda a: (
            int(a.analyzer.content_type == "paper" and source_info(a.item.source).family != "code"),
            *recommendation_quality_tier(a),
            -families[source_info(a.item.source).family],
            a.item.published_at.timestamp(),
        ))
        pool.remove(chosen)
        selected.append(chosen)
        categories.add(chosen.analyzer.primary_category)
        families[source_info(chosen.item.source).family] += 1
    selected.sort(key=recommendation_sort_key, reverse=True)
    logger.info("Weekly representative categories: %s", dict(Counter(a.analyzer.primary_category for a in selected)))
    logger.info(
        "Weekly representative information filter: candidates=%d qualified=%d "
        "filtered_sparse=%d selected=%d",
        candidate_count, len(qualified), candidate_count - len(qualified), len(selected),
    )
    return selected


def select_weekly_synthesis_candidates(
    candidates: list[AI4SAnalysis],
) -> list[AI4SAnalysis]:
    unique = list({a.item.url: a for a in candidates}.values())
    selected = select_daily_candidates(unique, WEEKLY_SYNTHESIS_CANDIDATE_LIMIT)
    # Include the research evidence before synthesis, even when higher scoring
    # ecosystem posts would otherwise consume all thirty positions.
    evidence = select_representative_works(unique)
    evidence_urls = {a.item.url for a in evidence}
    counts = Counter(a.analyzer.primary_category for a in selected)
    for candidate in evidence:
        if any(a.item.url == candidate.item.url for a in selected):
            continue
        category = candidate.analyzer.primary_category
        if len(selected) == WEEKLY_SYNTHESIS_CANDIDATE_LIMIT:
            replaceable = [a for a in reversed(selected) if a.item.url not in evidence_urls]
            same_domain = [a for a in replaceable if a.analyzer.primary_category == category]
            removed = (same_domain or [a for a in replaceable if counts[a.analyzer.primary_category] > 1])[0]
            selected.remove(removed)
            counts[removed.analyzer.primary_category] -= 1
        selected.append(candidate)
        counts[category] += 1
    selected.sort(key=recommendation_sort_key, reverse=True)
    logger.info("Weekly synthesis input categories: %s", dict(Counter(a.analyzer.primary_category for a in selected)))
    return selected


def _render_weekly_prompt(
    candidates: list[AI4SAnalysis], period_start: datetime, period_end: datetime
) -> str:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for analysis in candidates:
        summary = analysis.summary
        assert summary is not None
        grouped[analysis.analyzer.primary_category].append({
            "title": analysis.item.title,
            "url": analysis.item.url,
            "source": analysis.item.source,
            "published_at": analysis.item.published_at.isoformat(),
            "assessment": summary.assessment,
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
    if len(watchlist) > 5:
        raise LLMError("weekly synthesis watchlist must contain at most five questions")
    return overview, category_trends, watchlist


async def generate_weekly_report(
    storage: Storage,
    cfg: Config,
    report_date: date,
) -> dict[str, object]:
    period_start, period_end = weekly_period(report_date)
    candidates = storage.get_weekly_report_candidates(
        period_start,
        period_end,
        min_score=cfg.score_threshold,
    )
    existing = storage.get_report_by_period("weekly", period_start, period_end)
    if existing is not None:
        return _metrics(existing, len(candidates), created=False, llm_calls=0, cost_usd=0.0)

    logger.info("Weekly candidate categories: %s", dict(Counter(a.analyzer.primary_category for a in candidates)))
    synthesis_candidates = select_weekly_synthesis_candidates(candidates)
    representatives = select_representative_works(synthesis_candidates)
    if not synthesis_candidates:
        report, created = storage.create_report(
            "weekly", period_start, period_end, representatives
        )
        return _metrics(report, len(candidates), created=created, llm_calls=0, cost_usd=0.0)

    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    check_api_keys(cfg.models)
    data, cost_usd = await complete_json(
        model=cfg.models.summarizer,
        prompt=_render_weekly_prompt(synthesis_candidates, period_start, period_end),
        max_tokens=1600,
    )
    overview, category_trends, watchlist = _validate_synthesis(
        data,
        {analysis.analyzer.primary_category for analysis in synthesis_candidates},
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
