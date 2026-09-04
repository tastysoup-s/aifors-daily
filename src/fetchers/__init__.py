import asyncio
import logging
from typing import Any, Awaitable, Callable

from src.fetchers.arxiv import fetch_arxiv
from src.fetchers.github import fetch_github
from src.fetchers.hackernews import fetch_hackernews
from src.fetchers.rss import fetch_rss
from src.models import Item


logger = logging.getLogger(__name__)


FetcherFn = Callable[[dict[str, Any], int], Awaitable[list[Item]]]
FetchOutcome = tuple[str, str, bool, list[Item]]

# Map source type -> attribute name in this module.
# We do a dynamic lookup at call time so monkeypatch can rebind the name.
_REGISTRY: dict[str, str] = {
    "rss": "fetch_rss",
    "arxiv": "fetch_arxiv",
    "github": "fetch_github",
    "hackernews": "fetch_hackernews",
}


async def _run_one(source: dict[str, Any], window_hours: int) -> FetchOutcome:
    import sys
    src_type = source.get("type")
    name = source.get("name", "<unnamed>")
    fetcher_name = _REGISTRY.get(src_type)
    if fetcher_name is None:
        logger.warning("unknown source type %r for source %s", src_type, name)
        return name, str(src_type), False, []
    # Dynamic lookup from module namespace so monkeypatch works correctly.
    fetcher: FetcherFn = getattr(sys.modules[__name__], fetcher_name)
    try:
        items = await fetcher(source, window_hours)
        logger.info("source %s (%s) returned %d items", name, src_type, len(items))
        return name, str(src_type), True, items
    except Exception as exc:
        logger.error("source %s (%s) failed: %s", name, src_type, exc, exc_info=True)
        return name, str(src_type), False, []


async def fetch_all(
    sources: list[dict[str, Any]], window_hours: int
) -> list[Item]:
    if not sources:
        return []
    results = await asyncio.gather(
        *(_run_one(src, window_hours) for src in sources),
        return_exceptions=False,
    )
    items: list[Item] = []
    logger.info("Source fetch summary")
    for name, _src_type, succeeded, source_items in results:
        if succeeded:
            logger.info("%-32s fetched=%d", name, len(source_items))
        else:
            logger.warning("%-32s FAILED", name)
        items.extend(source_items)
    logger.info("total fetched=%d", len(items))
    return items
