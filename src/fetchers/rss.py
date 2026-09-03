import logging
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import feedparser
import httpx
from dateutil import parser as date_parser

from src.fetchers._http import USER_AGENT
from src.models import Item


logger = logging.getLogger(__name__)

_MAX_CONTENT_CHARS = 20_000
_REPEATED_LEAD_CHARS = 80
_IGNORED_TAGS = {"script", "style", "noscript"}
_BLOCK_TAGS = {"br", "div", "li", "p", "section"}


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORED_TAGS:
            self.ignored_depth += 1
        elif not self.ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS:
            self.ignored_depth = max(0, self.ignored_depth - 1)
        elif not self.ignored_depth and tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def _plain_text(value: Any) -> str:
    if not value:
        return ""
    parser = _PlainTextParser()
    parser.feed(str(value))
    text = " ".join("".join(parser.parts).split())
    return text.replace(" (opens in new tab)", "")


def choose_best_content(entry: dict[str, Any]) -> str:
    raw_content = entry.get("content") or []
    if not isinstance(raw_content, list):
        raw_content = [raw_content]

    content_options = []
    for part in raw_content:
        value = part.get("value") if isinstance(part, dict) else part
        cleaned = _plain_text(value)
        if cleaned:
            content_options.append(cleaned)

    candidates = [
        max(content_options, key=len, default=""),
        _plain_text(entry.get("summary")),
        _plain_text(entry.get("description")),
    ]
    selected = next((text for text in candidates if text), "")
    if len(selected) >= _REPEATED_LEAD_CHARS * 2:
        lead = selected[:_REPEATED_LEAD_CHARS]
        repeated_at = selected.find(lead, _REPEATED_LEAD_CHARS)
        if repeated_at != -1:
            selected = selected[repeated_at:]
    if selected.endswith(" Source"):
        selected = selected[:-len(" Source")]
    return selected[:_MAX_CONTENT_CHARS].strip()


def _parse_date(entry: dict) -> datetime | None:
    raw = entry.get("published") or entry.get("updated") or entry.get("pubDate")
    if not raw:
        if "published_parsed" in entry and entry.get("published_parsed"):
            return datetime(*entry["published_parsed"][:6], tzinfo=timezone.utc)
        return None
    try:
        dt = date_parser.parse(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def fetch_rss(source: dict[str, Any], window_hours: int) -> list[Item]:
    url = source["url"]
    name = source["name"]
    headers = {"User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        body = response.text

    feed = feedparser.parse(body)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    items: list[Item] = []
    for entry in feed.entries:
        published_at = _parse_date(entry)
        if published_at is None:
            logger.debug("rss:%s skip entry without date: %s", name, entry.get("link"))
            continue
        if published_at < cutoff:
            continue
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        items.append(
            Item(
                url=link,
                title=title,
                content=choose_best_content(entry),
                published_at=published_at,
                source=f"rss:{name}",
                raw=dict(entry),
            )
        )
    return items
