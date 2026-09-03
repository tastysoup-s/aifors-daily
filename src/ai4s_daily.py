import logging
from datetime import date, datetime, time, timezone

from src.config import Config
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
    existing = storage.get_report_by_period("daily", period_start, period_end)
    if existing is not None:
        return _metrics(existing, len(candidates), created=False)

    selected = candidates[:cfg.top_n]
    report, created = storage.create_report(
        "daily", period_start, period_end, selected
    )
    result = _metrics(report, len(candidates), created=created)
    logger.info(
        "daily report: period=%s candidates=%d selected=%d report_id=%d created=%s",
        report_date.isoformat(),
        result["candidates"],
        result["selected"],
        result["report_id"],
        created,
    )
    return result


def _metrics(report, candidates: int, *, created: bool) -> dict[str, object]:
    return {
        "period": report.period_start.date().isoformat(),
        "candidates": candidates,
        "selected": len(report.items),
        "categories": sorted({item.category for item in report.items}),
        "report_id": report.id,
        "created": created,
    }
