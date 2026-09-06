import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import feedparser
import httpx
from dateutil import parser as date_parser

from src.fetchers._http import USER_AGENT
from src.models import Item


logger = logging.getLogger(__name__)

_API_URL = "https://export.arxiv.org/api/query"
_TIMEOUT_SECONDS = 60.0  # arxiv export endpoint is sometimes slow
_RETRY_DELAY_SECONDS = 5.0  # back off once on transient failures


def _build_query(categories: list[str], terms: list[str] | None = None) -> str:
    parts = [f"cat:{c}" for c in categories]
    category_clause = " OR ".join(parts)
    terms = [term.strip().replace('"', '') for term in (terms or []) if term.strip()]
    if not terms:
        return category_clause
    term_clause = " OR ".join(f'all:"{term}"' for term in terms)
    return f"({category_clause}) AND ({term_clause})" if parts else f"({term_clause})"


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        sc = exc.response.status_code
        return sc == 429 or sc >= 500
    return False


async def fetch_arxiv(source: dict[str, Any], window_hours: int) -> list[Item]:
    name = source["name"]
    categories = source.get("categories", ["cs.AI"])
    max_results = int(source.get("max_results", 50))
    params = {
        "search_query": _build_query(categories, source.get("terms")),
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{_API_URL}?{urlencode(params)}"

    headers = {"User-Agent": USER_AGENT}
    body: str | None = None
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers=headers) as client:
        for attempt in range(2):
            try:
                response = await client.get(url)
                response.raise_for_status()
                body = response.text
                break
            except Exception as e:
                if attempt == 1 or not _is_transient(e):
                    raise
                logger.warning(
                    "arxiv attempt 1 transient failure (%s); retrying in %.1fs",
                    type(e).__name__, _RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(_RETRY_DELAY_SECONDS)

    assert body is not None
    feed = feedparser.parse(body)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    items: list[Item] = []
    for entry in feed.entries:
        published_raw = entry.get("published") or entry.get("updated")
        if not published_raw:
            continue
        try:
            published_at = date_parser.parse(published_raw)
        except (ValueError, TypeError):
            continue
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        if published_at < cutoff:
            continue
        link = entry.get("link") or entry.get("id")
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        items.append(
            Item(
                url=link,
                title=title,
                content=(entry.get("summary") or "").strip(),
                published_at=published_at,
                source=f"arxiv:{name}",
                raw=dict(entry),
            )
        )
    return items
