import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from dateutil import parser as date_parser

from src.fetchers._http import USER_AGENT
from src.models import Item


logger = logging.getLogger(__name__)

_API_URL = "https://api.github.com/search/repositories"


async def fetch_github(source: dict[str, Any], window_hours: int) -> list[Item]:
    name = source["name"]
    topic = source["topic"]
    min_stars = int(source.get("min_stars", 10))

    # Approximate "trending": repos pushed within window, sorted by stars.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    pushed_filter = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    query = f"topic:{topic} stars:>={min_stars} pushed:>={pushed_filter}"

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    params = {"q": query, "sort": "stars", "order": "desc", "per_page": 30}

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(_API_URL, params=params, headers=headers)
        response.raise_for_status()
        payload = response.json()

    items: list[Item] = []
    for repo in payload.get("items", []):
        pushed_raw = repo.get("pushed_at")
        if not pushed_raw:
            continue
        try:
            pushed_at = date_parser.parse(pushed_raw)
        except (ValueError, TypeError):
            continue
        if pushed_at.tzinfo is None:
            pushed_at = pushed_at.replace(tzinfo=timezone.utc)
        if pushed_at < cutoff:
            continue
        if int(repo.get("stargazers_count", 0)) < min_stars:
            continue
        content_parts = []
        description = (repo.get("description") or "").strip()
        if description:
            content_parts.append(description)
        topics = [
            str(topic).strip()
            for topic in (repo.get("topics") or [])
            if str(topic).strip()
        ][:8]
        if topics:
            content_parts.append(f"Topics: {', '.join(dict.fromkeys(topics))}")
        language = (repo.get("language") or "").strip()
        if language:
            content_parts.append(f"Language: {language}")
        homepage = (repo.get("homepage") or "").strip()
        if homepage:
            content_parts.append(f"Homepage: {homepage}")
        items.append(
            Item(
                url=repo["html_url"],
                title=repo["full_name"],
                content="\n\n".join(content_parts),
                published_at=pushed_at,
                source=f"github:{name}",
                raw=repo,
            )
        )
    return items
