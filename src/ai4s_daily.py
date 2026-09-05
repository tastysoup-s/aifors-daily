import logging
from collections import Counter
from datetime import date, datetime, time, timezone

from src.config import Config
from src.information_sufficiency import (
    information_score,
    insufficient_information_reason,
    recommendation_quality_tier,
    recommendation_sort_key,
)
from src.models import AI4SAnalysis, Report
from src.source_info import source_info
from src.storage import Storage


logger = logging.getLogger(__name__)


def daily_period(report_date: date) -> tuple[datetime, datetime]:
    start = datetime.combine(report_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(report_date, time.max, tzinfo=timezone.utc)
    return start, end


def generate_daily_report(
    storage: Storage,
    cfg: Config,
    report_date: date,
) -> dict[str, object]:
    period_start, period_end = daily_period(report_date)
    candidates = storage.get_report_candidates(
        period_start,
        period_end,
        min_score=cfg.score_threshold,
    )
    qualified = []
    sparse = []
    for candidate in candidates:
        reason = insufficient_information_reason(candidate)
        if reason is None:
            qualified.append(candidate)
        else:
            sparse.append((candidate, reason))
    existing = storage.get_report_by_period("daily", period_start, period_end)
    if existing is not None:
        # Qualification metrics describe current candidates, not a rewrite of
        # the already persisted selection (which may predate this rule).
        result = _metrics(existing, len(candidates), len(qualified), created=False)
        logger.info("existing Daily report reused; information filter not applied")
        _log_source_diversity(existing)
        return result

    for candidate, reason in sparse:
        logger.info(
            "filtered sparse: %s reason=%s information_score=%d",
            candidate.item.title, reason, information_score(candidate),
        )
    selected = select_daily_candidates(qualified, cfg.top_n)
    report, created = storage.create_report(
        "daily", period_start, period_end, selected
    )
    result = _metrics(report, len(candidates), len(qualified), created=created)
    logger.info(
        "Daily information filter: candidates=%d qualified=%d filtered_sparse=%d selected=%d",
        result["candidates"], result["qualified"], result["filtered_sparse"],
        result["selected"],
    )
    logger.info(
        "daily report: period=%s candidates=%d selected=%d report_id=%d created=%s",
        report_date.isoformat(),
        result["candidates"],
        result["selected"],
        result["report_id"],
        created,
    )
    _log_source_diversity(report)
    return result


def select_daily_candidates(
    candidates: list[AI4SAnalysis], limit: int
) -> list[AI4SAnalysis]:
    """Rank for information quality, then diversify exact quality ties."""
    candidates = sorted(candidates, key=recommendation_sort_key, reverse=True)
    selected: list[AI4SAnalysis] = []
    family_counts: Counter[str] = Counter()
    start = 0
    while start < len(candidates) and len(selected) < limit:
        tier_key = recommendation_quality_tier(candidates[start])
        end = start
        while (
            end < len(candidates)
            and recommendation_quality_tier(candidates[end]) == tier_key
        ):
            end += 1
        tier = list(candidates[start:end])
        while tier and len(selected) < limit:
            chosen_index = min(
                range(len(tier)),
                key=lambda index: (
                    family_counts[source_info(tier[index].item.source).family],
                    index,
                ),
            )
            chosen = tier.pop(chosen_index)
            selected.append(chosen)
            family_counts[source_info(chosen.item.source).family] += 1
        start = end
    return selected


def _source_diversity(report: Report) -> tuple[Counter[str], set[str]]:
    source_counts: Counter[str] = Counter()
    families: set[str] = set()
    for report_item in report.items:
        info = source_info(report_item.analysis.item.source)
        source_counts[info.display_name] += 1
        families.add(info.family)
    return source_counts, families


def _log_source_diversity(report: Report) -> None:
    source_counts, families = _source_diversity(report)
    logger.info(
        "Daily source diversity: items=%d unique_sources=%d source_families=%d",
        len(report.items),
        len(source_counts),
        len(families),
    )
    for name, count in source_counts.most_common():
        logger.info("daily source: %s=%d", name, count)


def _metrics(
    report: Report, candidates: int, qualified: int, *, created: bool
) -> dict[str, object]:
    source_counts, families = _source_diversity(report)
    return {
        "period": report.period_start.date().isoformat(),
        "candidates": candidates,
        "qualified": qualified,
        "filtered_sparse": candidates - qualified,
        "selected": len(report.items),
        "categories": sorted({item.category for item in report.items}),
        "unique_sources": len(source_counts),
        "source_families": len(families),
        "source_counts": dict(source_counts),
        "report_id": report.id,
        "created": created,
    }
