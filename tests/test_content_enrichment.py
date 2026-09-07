from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from src.content_enrichment import (
    clean_github_readme,
    enrich_item_content,
    extract_official_page_text,
    extract_scientific_article_text,
)
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
async def test_arxiv_uses_official_full_text_even_with_long_abstract(monkeypatch):
    html = """<article><section class="ltx_abstract"><p>Full abstract evidence.</p></section>
    <h2>Methods</h2><p>Equivariant network details from the full article. """ + "method detail " * 80 + """</p>
    <h2>Results</h2><p>Full-text benchmark improves error by 18 percent. """ + "result detail " * 80 + """</p></article>"""
    fetch = AsyncMock(return_value=html)
    monkeypatch.setattr("src.content_enrichment._fetch_text", fetch)
    item = _item(
        source="arxiv:physics",
        content="Detailed abstract. " * 60,
        url="https://arxiv.org/abs/1234.5678",
    )

    result = await enrich_item_content(item)

    assert result.method == "arxiv-html"
    assert "Equivariant network details" in result.text
    assert "18 percent" in result.text
    fetch.assert_awaited_once_with("https://arxiv.org/html/1234.5678")


@pytest.mark.asyncio
async def test_arxiv_full_text_failure_falls_back_to_abstract(monkeypatch):
    request = httpx.Request("GET", "https://arxiv.org/html/1234.5678")
    monkeypatch.setattr("src.content_enrichment._fetch_text",
                        AsyncMock(side_effect=httpx.ConnectError("offline", request=request)))
    item = _item(source="arxiv:physics", content="Detailed abstract evidence.",
                 url="https://arxiv.org/abs/1234.5678")
    result = await enrich_item_content(item)
    assert result.text == item.content and result.method is None


def test_section_aware_truncation_keeps_methods_results_before_filler():
    filler = "Background filler without evidence. " * 100
    html = (f"<article><h2>Appendix</h2><p>{filler}</p>"
            "<h2>Methods</h2><p>METHOD_EVIDENCE neural operator training protocol.</p>"
            "<h2>Results</h2><p>RESULT_EVIDENCE beats the baseline by 21 percent.</p>"
            "<h2>Conclusion</h2><p>CONCLUSION_EVIDENCE validated across datasets.</p></article>")
    text = extract_scientific_article_text(html, max_chars=400)
    assert "METHOD_EVIDENCE" in text and "RESULT_EVIDENCE" in text
    assert text.index("METHOD_EVIDENCE") < text.index("RESULT_EVIDENCE")
    assert len(text) <= 400 and "Background filler" not in text


def test_github_cleanup_prioritizes_results_and_drops_installation_section():
    readme = """# Project
    Scientific machine learning for molecular discovery.
    ## Installation
    Download packages and configure environments. This is setup prose.
    ## Benchmark Results
    The model reduces force error by 14 percent on the held-out benchmark.
    """
    text = clean_github_readme(readme)
    assert "reduces force error" in text
    assert "setup prose" not in text


def test_rss_cleanup_discards_navigation_author_and_footer():
    html = """<nav>Subscribe Careers</nav><article><h2>Methods</h2>
    <p>A neural surrogate models fluid dynamics.</p><h2>Results</h2>
    <p>Error fell by 12 percent.</p><h2>Author biography</h2>
    <p>The author enjoys hiking and public speaking.</p></article><footer>Cookie Privacy</footer>"""
    text = extract_official_page_text(html)
    assert "neural surrogate" in text and "12 percent" in text
    assert "hiking" not in text and "Cookie" not in text and "Subscribe" not in text


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
