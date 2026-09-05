from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from src.content_enrichment import enrich_item_content, extract_official_page_text
from src.models import Item


def _item(*, source: str, content: str, url: str) -> Item:
    return Item(
        url=url,
        title="Scientific project",
        content=content,
        published_at=datetime.now(timezone.utc),
        source=source,
        raw={
            "description": "Molecular simulation toolkit",
            "topics": ["ai-for-science", "molecular-dynamics"],
            "homepage": "https://example.org/project",
            "stargazers_count": 4321,
            "language": "Python",
        },
    )


@pytest.mark.asyncio
async def test_short_github_content_is_enriched_with_clean_readme(monkeypatch):
    readme = """
    [![build](https://img.shields.io/build.svg)](https://example.com)
    # NequIP
    NequIP learns E(3)-equivariant interatomic potentials from atomic data.
    ## Results
    The benchmark reports force and energy errors on the evaluation set.
    ```bash
    pip install nequip
    ```
    """
    fetch = AsyncMock(return_value=readme)
    monkeypatch.setattr("src.content_enrichment._fetch_text", fetch)
    item = _item(
        source="github:materials",
        content="E(3)-equivariant interatomic potentials.",
        url="https://github.com/mir-group/nequip",
    )

    result = await enrich_item_content(item)

    assert result.method == "github-readme"
    assert result.enriched_chars > result.original_chars
    assert "benchmark reports force and energy errors" in result.text
    assert "Stars: 4321" in result.text
    assert "shields.io" not in result.text
    assert "pip install" not in result.text
    fetch.assert_awaited_once_with(
        "https://api.github.com/repos/mir-group/nequip/readme",
        accept="application/vnd.github.raw+json",
    )


@pytest.mark.asyncio
async def test_rss_official_page_failure_keeps_original_content(monkeypatch):
    request = httpx.Request("GET", "https://lab.example/article")
    monkeypatch.setattr(
        "src.content_enrichment._fetch_text",
        AsyncMock(side_effect=httpx.ConnectError("offline", request=request)),
    )
    item = _item(
        source="rss:research-lab",
        content="Short official announcement.",
        url="https://lab.example/article",
    )

    result = await enrich_item_content(item)

    assert result.text == "Short official announcement."
    assert result.method is None
    assert result.enriched_chars == result.original_chars


@pytest.mark.asyncio
async def test_long_arxiv_abstract_does_not_trigger_enrichment(monkeypatch):
    fetch = AsyncMock()
    monkeypatch.setattr("src.content_enrichment._fetch_text", fetch)
    item = _item(
        source="arxiv:physics",
        content="Detailed abstract. " * 60,
        url="https://arxiv.org/abs/1234.5678",
    )

    result = await enrich_item_content(item)

    assert result.method is None
    assert result.text == item.content.strip()
    fetch.assert_not_awaited()


def test_official_page_extraction_prefers_article_and_removes_navigation():
    html = """
    <nav>Subscribe About Careers</nav>
    <article><h1>Model release</h1><p>We evaluate the model on three datasets.</p>
    <p>The reported error decreases by 18 percent.</p></article>
    <footer>Privacy policy</footer>
    """

    text = extract_official_page_text(html)

    assert "three datasets" in text
    assert "18 percent" in text
    assert "Subscribe" not in text
    assert "Privacy policy" not in text
