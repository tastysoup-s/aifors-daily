"""Optional, bounded discovery searches using the Tavily HTTP API."""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from dateutil import parser as date_parser

from src.models import Item


logger = logging.getLogger(__name__)
MAX_QUERIES_PER_RUN = 7
MAX_RESULTS_PER_QUERY = 5


def _parse_result(result: object, query: str, category: str, rank: int,
                  now: datetime, cutoff: datetime) -> Item | None:
    if not isinstance(result, dict):
        return None
    url, title = result.get("url"), result.get("title")
    if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        domain = parsed.hostname.casefold().removeprefix("www.")
    except ValueError:
        return None
    published_raw = result.get("published_date")
    published_at = now
    if published_raw:
        try:
            published_at = date_parser.parse(published_raw)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            published_at = published_at.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None
        if not cutoff <= published_at <= now:
            return None
    content = result.get("content")
    raw = dict(result)
    raw.update(
        retrieved_via="tavily", retrieval_query=query,
        retrieval_category_hint=category, result_rank=rank,
        original_domain=domain, retrieved_at=now.isoformat(),
        publication_date_known=bool(published_raw),
    )
    return Item(url=url.strip(), title=title.strip(),
                content=content if isinstance(content, str) else "",
                published_at=published_at, source=f"tavily:{domain}", raw=raw)


async def fetch_tavily(source: dict[str, Any], window_hours: int) -> list[Item]:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        logger.info("Tavily disabled: TAVILY_API_KEY missing")
        return []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    queries = list(source.get("queries", {}).items())[:MAX_QUERIES_PER_RUN]
    max_results = min(MAX_RESULTS_PER_QUERY, max(1, int(source.get("max_results", 5))))
    items: list[Item] = []
    async with httpx.AsyncClient(timeout=20.0) as client:
        for category, query in queries:
            if not isinstance(query, str) or not query.strip():
                continue
            try:
                response = await client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"query": query, "topic": "general", "search_depth": "basic",
                          "max_results": max_results, "start_date": cutoff.date().isoformat(),
                          "end_date": now.date().isoformat(), "auto_parameters": False,
                          "include_answer": False, "include_raw_content": False},
                )
                response.raise_for_status()
                results = response.json().get("results", [])
                if not isinstance(results, list):
                    raise ValueError("results must be a list")
            except (httpx.HTTPError, ValueError, AttributeError, OSError) as error:
                # Do not log provider bodies or exception text, which may contain credentials.
                logger.warning("Tavily query %s failed: %s", category, type(error).__name__)
                continue
            parsed_items = [
                item for rank, result in enumerate(results[:max_results], 1)
                if (item := _parse_result(result, query, category, rank, now, cutoff))
            ]
            items.extend(parsed_items)
            logger.info("Tavily query %s fetched=%d", category, len(parsed_items))
    return items
