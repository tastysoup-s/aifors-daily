import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx

from src.fetchers._http import USER_AGENT
from src.models import Item


logger = logging.getLogger(__name__)

_MIN_CONTENT_CHARS = 500
_MAX_ENRICHED_CHARS = 40_000
_BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "li", "p"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_IGNORED_TAGS = {"aside", "footer", "form", "nav", "noscript", "script", "style", "svg"}
_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LOW_VALUE_MARKERS = (
    "install", "getting started", "quick start", "usage", "contribut", "license",
    "citation", "acknowledg", "author", "cookie", "privacy", "career", "subscribe",
)


@dataclass(frozen=True)
class EnrichedContent:
    text: str
    original_chars: int
    enriched_chars: int
    method: str | None = None


@dataclass(frozen=True)
class _TextBlock:
    heading: str
    text: str
    order: int


class _ScientificTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_TextBlock] = []
        self.ignored_depth = 0
        self.abstract_depth = 0
        self._abstract_tags: Counter[str] = Counter()
        self.current_heading = ""
        self._active_tag: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ignored = tag in _IGNORED_TAGS
        if ignored:
            self.ignored_depth += 1
        classes = " ".join(value or "" for key, value in attrs if key == "class").casefold()
        abstract = "abstract" in classes
        if abstract:
            self.abstract_depth += 1
            self._abstract_tags[tag] += 1
        if self.ignored_depth or tag not in _BLOCK_TAGS or self._active_tag is not None:
            return
        self._active_tag = tag
        self._parts = []

    def handle_endtag(self, tag: str) -> None:
        if not self.ignored_depth and tag == self._active_tag:
            value = " ".join("".join(self._parts).split())
            if value:
                if tag in _HEADING_TAGS:
                    self.current_heading = value
                else:
                    heading = "Abstract" if self.abstract_depth else self.current_heading
                    self.blocks.append(_TextBlock(heading, value, len(self.blocks)))
            self._active_tag = None
            self._parts = []
        if self._abstract_tags[tag]:
            self.abstract_depth = max(0, self.abstract_depth - 1)
            self._abstract_tags[tag] -= 1
        if tag in _IGNORED_TAGS:
            self.ignored_depth = max(0, self.ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and self._active_tag is not None:
            self._parts.append(data)


def _section_priority(heading: str) -> int:
    value = heading.casefold()
    if "abstract" in value:
        return 0
    if any(marker in value for marker in ("method", "methodology", "approach")):
        return 1
    if any(marker in value for marker in ("experiment", "evaluation", "result", "benchmark", "ablation")):
        return 2
    if "conclusion" in value:
        return 3
    if "introduction" in value or "background" in value:
        return 4
    if "discussion" in value or "limitation" in value:
        return 5
    if any(marker in value for marker in _LOW_VALUE_MARKERS):
        return 8
    return 6


def _pack_blocks(blocks: list[_TextBlock], max_chars: int = _MAX_ENRICHED_CHARS) -> str:
    unique: list[_TextBlock] = []
    seen: set[str] = set()
    for block in blocks:
        normalized = block.text.casefold()
        if normalized in seen or len(block.text) < 20:
            continue
        seen.add(normalized)
        unique.append(block)
    if any(_section_priority(block.heading) < 8 for block in unique):
        unique = [block for block in unique if _section_priority(block.heading) < 8]
    ordered = sorted(unique, key=lambda block: (_section_priority(block.heading), block.order))
    parts: list[str] = []
    used = 0
    previous_heading = None
    for block in ordered:
        prefix = f"[Section: {block.heading}]\n" if block.heading and block.heading != previous_heading else ""
        value = prefix + block.text
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(value) > remaining:
            if remaining >= 200:
                parts.append(value[:remaining])
            break
        parts.append(value)
        used += len(value) + 2
        previous_heading = block.heading
    return "\n\n".join(parts)[:max_chars]


def extract_scientific_article_text(html: str, max_chars: int = _MAX_ENRICHED_CHARS) -> str:
    parser = _ScientificTextParser()
    parser.feed(html)
    return _pack_blocks(parser.blocks, max_chars=max_chars)


def extract_official_page_text(html: str) -> str:
    return extract_scientific_article_text(html)


def clean_github_readme(markdown: str) -> str:
    blocks: list[_TextBlock] = []
    heading = "Project overview"
    parts: list[str] = []
    in_code_block = False

    def flush() -> None:
        if parts:
            blocks.append(_TextBlock(heading, " ".join(parts), len(blocks)))
            parts.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block or "shields.io" in line or line.startswith(("![", "[![")):
            continue
        if line.startswith("#"):
            flush()
            heading = line.lstrip("# ").strip() or heading
            continue
        line = _MARKDOWN_LINK.sub(lambda match: match.group(1), line)
        line = line.lstrip(">*- ").strip()
        if line:
            parts.append(line)
    flush()
    return _pack_blocks(blocks)


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
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


def _arxiv_html_url(url: str) -> str:
    parsed = urlsplit(url)
    identifier = parsed.path.removeprefix("/abs/").removeprefix("/html/").strip("/")
    return f"https://arxiv.org/html/{identifier}"


async def _enrich_github(item: Item) -> str:
    parsed = urlsplit(item.url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc.casefold() != "github.com" or len(path_parts) < 2:
        return item.content
    owner, repo = path_parts[:2]
    readme = clean_github_readme(await _fetch_text(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        accept="application/vnd.github.raw+json",
    ))
    return "\n\n".join(part for part in (_github_metadata(item), readme) if part)[:_MAX_ENRICHED_CHARS]


async def enrich_item_content(item: Item) -> EnrichedContent:
    original = item.content.strip()
    enriched = original
    method = None
    try:
        if item.source.startswith("arxiv:"):
            enriched = extract_scientific_article_text(await _fetch_text(_arxiv_html_url(item.url)))
            method = "arxiv-html"
        elif item.source.startswith("github:"):
            enriched = await _enrich_github(item)
            method = "github-readme"
        elif item.source.startswith(("rss:", "tavily:")):
            enriched = extract_scientific_article_text(await _fetch_text(item.url))
            method = "official-page"
        elif len(original) < _MIN_CONTENT_CHARS:
            enriched = original
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("content enrichment failed for %s: %s", item.url, error)
        enriched = original
        method = None

    if len(enriched) <= len(original):
        enriched = original
        method = None
    return EnrichedContent(enriched, len(original), len(enriched), method)
