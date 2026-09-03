import argparse
import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.ai4s_analyzer import run_ai4s_analyze
from src.config import load_config
from src.dedup import dedup_by_url
from src.fetchers import fetch_all
from src.logging_setup import setup_logging
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
            "fetch summary: fetched=%d deduped=%d stored=%d",
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai-daily")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_ in (
        ("fetch", "Fetch all sources and store items"),
        ("analyze", "Classify and score unanalyzed items for AI4S"),
        ("summarize", "Score unscored items and summarize the top-N"),
        ("render", "Render summarized items to site/index.html"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--sources", default="config/sources.yaml")
        p.add_argument("--preferences", default="config/preferences.yaml")
        p.add_argument("--db", default="data/ai_daily.db")
        if name == "analyze":
            p.add_argument("--limit", type=_positive_int)
        if name == "render":
            p.add_argument("--output-dir", default="site")
            p.add_argument("--within-days", type=int, default=30)

    return parser.parse_args(argv)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
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
