import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from src.ai4s_analyzer import run_ai4s_analyze
from src.ai4s_daily import generate_daily_report
from src.ai4s_summarizer import run_ai4s_summarize
from src.ai4s_summary_enrichment import (
    MAX_ENRICHMENT_ITEMS,
    run_ai4s_summary_enrichment,
)
from src.ai4s_weekly import generate_weekly_report, latest_weekly_report_date
from src.config import load_config
from src.dedup import dedup_by_url
from src.fetchers import fetch_all
from src.logging_setup import setup_logging
from src.notifier.ai4s_web import render_ai4s_site
from src.notifier.web import render_site
from src.storage import Storage
from src.summarizer import run_summarize


logger = logging.getLogger(__name__)


async def run_fetch(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
) -> dict[str, int]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        items = await fetch_all(config.sources, window_hours=config.fetch_window_hours)
        fetched = len(items)
        unique = dedup_by_url(items, storage)
        deduped = len(unique)
        storage.record_items(unique)
        stored = deduped  # records dedup further by PK but our dedup_by_url already aligned
        logger.info(
            "fetch summary: total fetched=%d new items=%d stored=%d",
            fetched, deduped, stored,
        )
        return {"fetched": fetched, "deduped": deduped, "stored": stored}
    finally:
        storage.close()


async def run_summarize_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
) -> dict:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return await run_summarize(storage, config)
    finally:
        storage.close()


async def run_analyze_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    limit: int | None = None,
) -> dict[str, int | float]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return await run_ai4s_analyze(storage, config, limit=limit)
    finally:
        storage.close()


async def run_ai4s_summarize_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    limit: int | None = None,
) -> dict[str, int | float]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return await run_ai4s_summarize(storage, config, limit=limit)
    finally:
        storage.close()


async def run_ai4s_summary_enrichment_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    limit: int = MAX_ENRICHMENT_ITEMS,
) -> dict[str, int | float]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return await run_ai4s_summary_enrichment(storage, config, limit=limit)
    finally:
        storage.close()


def run_generate_daily_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    report_date: date | None = None,
) -> dict[str, object]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return generate_daily_report(
            storage, config, report_date or datetime.now(timezone.utc).date()
        )
    finally:
        storage.close()


async def run_generate_weekly_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    report_date: date | None = None,
) -> dict[str, object]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        resolved_date = report_date or latest_weekly_report_date(
            datetime.now(timezone.utc).date()
        )
        return await generate_weekly_report(storage, config, resolved_date)
    finally:
        storage.close()


async def run_render_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    output_dir: Path = Path("site"),
    within_days: int = 30,
) -> dict:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return render_site(
            storage,
            min_score=config.score_threshold,
            within_days=within_days,
            top_n=10_000,  # all summarized items; threshold/within_days do the filtering
            output_dir=output_dir,
        )
    finally:
        storage.close()


def run_render_ai4s_cmd(
    sources_path: Path = Path("config/sources.yaml"),
    preferences_path: Path = Path("config/preferences.yaml"),
    db_path: Path = Path("data/ai_daily.db"),
    output_dir: Path = Path("site"),
) -> dict[str, object]:
    config = load_config(sources_path=sources_path, preferences_path=preferences_path)
    storage = Storage(db_path)
    storage.init()
    try:
        return render_ai4s_site(
            storage,
            sources=config.sources,
            output_dir=output_dir,
        )
    finally:
        storage.close()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai-daily")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_ in (
        ("fetch", "Fetch all sources and store items"),
        ("analyze", "Classify and score unanalyzed items for AI4S"),
        ("summarize", "Score unscored items and summarize the top-N"),
        ("summarize-ai4s", "Summarize high-scoring AI4S analyses"),
        ("enrich-ai4s-summaries", "Enrich recent legacy AI4S summaries"),
        ("generate-daily", "Generate a persisted AI4S daily report"),
        ("generate-weekly", "Generate a persisted AI4S weekly report"),
        ("render-ai4s", "Render persisted AI4S reports to site/index.html"),
        ("render", "Render summarized items to site/index.html"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--sources", default="config/sources.yaml")
        p.add_argument("--preferences", default="config/preferences.yaml")
        p.add_argument("--db", default="data/ai_daily.db")
        if name in ("analyze", "summarize-ai4s"):
            p.add_argument("--limit", type=_positive_int)
        if name == "enrich-ai4s-summaries":
            p.add_argument("--limit", type=_enrichment_limit, default=MAX_ENRICHMENT_ITEMS)
        if name in ("generate-daily", "generate-weekly"):
            date_type = _weekly_date if name == "generate-weekly" else _iso_date
            p.add_argument("--report-date", type=date_type)
        if name in ("render", "render-ai4s"):
            p.add_argument("--output-dir", default="site")
        if name == "render":
            p.add_argument("--within-days", type=int, default=30)

    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _enrichment_limit(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > MAX_ENRICHMENT_ITEMS:
        raise argparse.ArgumentTypeError(
            f"must not exceed {MAX_ENRICHMENT_ITEMS}"
        )
    return parsed


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be YYYY-MM-DD") from error


def _weekly_date(value: str) -> date:
    parsed = _iso_date(value)
    if parsed.weekday() not in (2, 6):
        raise argparse.ArgumentTypeError(
            "weekly report date must be a Wednesday or Sunday"
        )
    return parsed


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # local dev convenience; no-op in GitHub Actions
    setup_logging()
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "fetch":
        asyncio.run(run_fetch(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
        ))
        return 0
    if args.command == "summarize":
        asyncio.run(run_summarize_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
        ))
        return 0
    if args.command == "analyze":
        asyncio.run(run_analyze_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            limit=args.limit,
        ))
        return 0
    if args.command == "summarize-ai4s":
        asyncio.run(run_ai4s_summarize_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            limit=args.limit,
        ))
        return 0
    if args.command == "enrich-ai4s-summaries":
        result = asyncio.run(run_ai4s_summary_enrichment_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            limit=args.limit,
        ))
        print(
            f"candidates={result['candidates']} selected={result['selected']} "
            f"enriched={result['enriched']} qualified_after={result['qualified_after']} "
            f"errors={result['errors']} cost=${result['cost_usd']:.6f}"
        )
        return 0
    if args.command == "generate-daily":
        result = run_generate_daily_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            report_date=args.report_date,
        )
        print(
            f"period={result['period']} candidates={result['candidates']} "
            f"selected={result['selected']} categories={','.join(result['categories'])} "
            f"report_id={result['report_id']} created={str(result['created']).lower()}"
        )
        return 0
    if args.command == "generate-weekly":
        result = asyncio.run(run_generate_weekly_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            report_date=args.report_date,
        ))
        print(
            f"period={result['period']} candidates={result['candidates']} "
            f"representatives={result['representatives']} "
            f"categories={','.join(result['categories'])} "
            f"report_id={result['report_id']} created={str(result['created']).lower()} "
            f"llm_calls={result['llm_calls']} cost=${result['cost_usd']:.6f}"
        )
        return 0
    if args.command == "render-ai4s":
        result = run_render_ai4s_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            output_dir=Path(args.output_dir),
        )
        print(
            f"daily_items={result['daily_items']} weekly_items={result['weekly_items']} "
            f"output={result['output']}"
        )
        return 0
    if args.command == "render":
        result = asyncio.run(run_render_cmd(
            sources_path=Path(args.sources),
            preferences_path=Path(args.preferences),
            db_path=Path(args.db),
            output_dir=Path(args.output_dir),
            within_days=args.within_days,
        ))
        print(f"rendered={result['rendered']} output={result['output']}")
        return 0
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
