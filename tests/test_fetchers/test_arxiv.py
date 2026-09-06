import re

import httpx
import pytest
from freezegun import freeze_time

from src.fetchers.arxiv import fetch_arxiv


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_arxiv_returns_recent_papers(httpx_mock, arxiv_response_xml):
    httpx_mock.add_response(
        url=re.compile(r"https://export\.arxiv\.org/api/query.*"),
        text=arxiv_response_xml,
    )
    source = {
        "name": "arxiv-cs-ai",
        "type": "arxiv",
        "categories": ["cs.AI"],
        "max_results": 50,
    }
    items = await fetch_arxiv(source, window_hours=36)
    urls = [i.url for i in items]
    assert "http://arxiv.org/abs/2405.00001v1" in urls
    assert "http://arxiv.org/abs/2401.99999v1" not in urls
    new_item = next(i for i in items if i.url == "http://arxiv.org/abs/2405.00001v1")
    assert new_item.title == "A New Agent Framework"
    assert new_item.source == "arxiv:arxiv-cs-ai"
    assert "novel agent framework" in new_item.content


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_arxiv_retries_once_on_timeout(
    httpx_mock, arxiv_response_xml, monkeypatch
):
    # Avoid real sleep in test.
    monkeypatch.setattr("src.fetchers.arxiv._RETRY_DELAY_SECONDS", 0.0)
    # First call: simulated timeout. Second call: success.
    httpx_mock.add_exception(httpx.ReadTimeout("simulated arxiv slow"))
    httpx_mock.add_response(
        url=re.compile(r"https://export\.arxiv\.org/api/query.*"),
        text=arxiv_response_xml,
    )
    source = {
        "name": "arxiv-cs-ai", "type": "arxiv",
        "categories": ["cs.AI"], "max_results": 50,
    }
    items = await fetch_arxiv(source, window_hours=36)
    # Retry succeeded — got the same items as the happy-path test.
    urls = [i.url for i in items]
    assert "http://arxiv.org/abs/2405.00001v1" in urls


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_arxiv_does_not_retry_on_404(httpx_mock):
    # 404 is not transient — no retry, error propagates to the orchestrator.
    httpx_mock.add_response(
        url=re.compile(r"https://export\.arxiv\.org/api/query.*"),
        status_code=404,
    )
    source = {
        "name": "arxiv-cs-ai", "type": "arxiv",
        "categories": ["cs.AI"], "max_results": 50,
    }
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_arxiv(source, window_hours=36)


@pytest.mark.parametrize("categories,terms,expected", [
    (["cs.AI", "cs.LG"], None, "cat:cs.AI OR cat:cs.LG"),
    (["physics.chem-ph"], [], "cat:physics.chem-ph"),
    (["physics.chem-ph", "cond-mat.mtrl-sci"], ["machine learning", "neural network"],
     '(cat:physics.chem-ph OR cat:cond-mat.mtrl-sci) AND (all:"machine learning" OR all:"neural network")'),
])
def test_arxiv_query_builder(categories, terms, expected):
    from src.fetchers.arxiv import _build_query
    assert _build_query(categories, terms) == expected


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_terms_pass_through_http_query_without_changing_dates(httpx_mock, arxiv_response_xml):
    httpx_mock.add_response(text=arxiv_response_xml)
    items = await fetch_arxiv({"name": "physics", "categories": ["physics.comp-ph"],
                               "terms": ["surrogate model"]}, 36)
    query = httpx_mock.get_request().url.params["search_query"]
    assert query == '(cat:physics.comp-ph) AND (all:"surrogate model")'
    assert items[0].published_at.isoformat() == "2026-05-14T18:00:00+00:00"
