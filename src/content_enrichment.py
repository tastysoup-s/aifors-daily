import logging
import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from src.fetchers._http import USER_AGENT
from src.models import Item


logger = logging.getLogger(__name__)

_MIN_CONTENT_CHARS = 500
_MAX_ENRICHED_CHARS = 10_000
_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "li", "p"}
_IGNORED_TAGS = {
    "aside",
    "footer",
    "form",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
}
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


@dataclass(frozen=True)
class EnrichedContent:
    text: str
    original_chars: int
    enriched_chars: int
    method: str | None = None


class _ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.all_parts: list[str] = []
        self.article_parts: list[str] = []
        self.ignored_depth = 0
        self.article_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORED_TAGS:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag in {"article", "main"}:
            self.article_depth += 1
        if tag in _BLOCK_TAGS:
            self.all_parts.append("\n")
            if self.article_depth:
                self.article_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORED_TAGS:
            self.ignored_depth = max(0, self.ignored_depth - 1)
            return
        if self.ignored_depth:
            return
        if tag in _BLOCK_TAGS:
            self.all_parts.append("\n")
            if self.article_depth:
                self.article_parts.append("\n")
        if tag in {"article", "main"}:
            self.article_depth = max(0, self.article_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        self.all_parts.append(data)
        if self.article_depth:
            self.article_parts.append(data)


def _clean_lines(parts: list[str]) -> str:
    lines: list[str] = []
    for line in "".join(parts).splitlines():
        cleaned = " ".join(line.split())
        if cleaned and (not lines or cleaned != lines[-1]):
            lines.append(cleaned)
    return "\n\n".join(lines)


def extract_official_page_text(html: str) -> str:
    parser = _ArticleTextParser()
    parser.feed(html)
    article = _clean_lines(parser.article_parts)
    text = article if len(article) >= 200 else _clean_lines(parser.all_parts)
    return text[:_MAX_ENRICHED_CHARS]


def clean_github_readme(markdown: str) -> str:
    lines: list[str] = []
    in_code_block = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or "shields.io" in line:
            continue
        if line.startswith("![") or line.startswith("[!["):
            continue
        line = _MARKDOWN_LINK.sub(lambda match: match.group(1), line)
        line = line.lstrip("#>*- ").strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n\n".join(lines)[:_MAX_ENRICHED_CHARS]


def _github_metadata(item: Item) -> str:
    raw = item.raw
    parts = [item.content.strip()]
    facts = (
        ("Description", raw.get("description")),
        ("Topics", ", ".join(raw.get("topics") or [])),
        ("Homepage", raw.get("homepage")),
        ("Stars", raw.get("stargazers_count")),
        ("Language", raw.get("language")),
    )
    parts.extend(f"{label}: {value}" for label, value in facts if value)
    return "\n\n".join(dict.fromkeys(part for part in parts if part))


async def _fetch_text(url: str, *, accept: str = "text/html") -> str:
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if urlsplit(url).netloc.casefold() == "api.github.com":
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(
        timeout=20.0, follow_redirects=True, headers=headers
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def _enrich_github(item: Item) -> str:
    parsed = urlsplit(item.url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.casefold() != "github.com" or len(path_parts) < 2:
        return item.content
    owner, repo = path_parts[:2]
    readme_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    readme = clean_github_readme(
        await _fetch_text(readme_url, accept="application/vnd.github.raw+json")
    )
    return "\n\n".join(part for part in (_github_metadata(item), readme) if part)[
        :_MAX_ENRICHED_CHARS
    ]


async def enrich_item_content(item: Item) -> EnrichedContent:
    original = item.content.strip()
    if len(original) >= _MIN_CONTENT_CHARS:
        return EnrichedContent(original, len(original), len(original))

    try:
        if item.source.startswith("github:"):
            enriched = await _enrich_github(item)
            method = "github-readme"
        elif item.source.startswith(("rss:", "arxiv:")):
            enriched = extract_official_page_text(await _fetch_text(item.url))
            method = "official-page"
        else:
            enriched = original
            method = None
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("content enrichment failed for %s: %s", item.url, error)
        enriched = original
        method = None

    if len(enriched) <= len(original):
        enriched = original
        method = None
    return EnrichedContent(original if not method else enriched, len(original), len(enriched), method)
