import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from freezegun import freeze_time

from src.fetchers import fetch_all
from src.fetchers.tavily import fetch_tavily
from src.dedup import dedup_by_url
from src.content_enrichment import EnrichedContent
from src.source_info import source_info
from src.storage import Storage


@pytest.fixture
def source():
    return {"name": "discovery", "type": "tavily", "queries": {"physics": "neural simulation"}}


def result(**overrides):
    value = {"url": "https://www.nature.com/articles/test", "title": "Research",
             "content": "Measured scientific result.", "score": 0.82,
             "published_date": "2026-09-05T12:00:00Z"}
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_missing_key_skips_without_network(monkeypatch, source, caplog):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with caplog.at_level("INFO"):
        assert await fetch_tavily(source, 168) == []
    assert "Tavily disabled: TAVILY_API_KEY missing" in caplog.text


@pytest.mark.asyncio
@freeze_time("2026-09-06 12:00:00")
async def test_parsing_request_provenance_and_existing_dedup(monkeypatch, source, httpx_mock, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    httpx_mock.add_response(json={"results": [result(), result()]})
    items = await fetch_tavily(source, 168)
    item = items[0]
    assert item.source == "tavily:nature.com"
    assert item.published_at == datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    assert item.raw["retrieval_query"] == "neural simulation"
    assert item.raw["retrieval_category_hint"] == "physics"
    assert item.raw["result_rank"] == 1 and item.raw["score"] == 0.82
    assert item.raw["retrieved_via"] == "tavily"
    assert item.raw["original_domain"] == "nature.com"
    assert item.raw["retrieved_at"].startswith("2026-09-06")
    assert "primary_category" not in item.raw
    assert not hasattr(item, "primary_category")
    request = httpx_mock.get_request()
    assert str(request.url) == "https://api.tavily.com/search"
    assert request.headers["Authorization"] == "Bearer test-only"
    payload = json.loads(request.content)
    assert payload["max_results"] == 5
    assert payload["search_depth"] == "basic" and not payload["auto_parameters"]
    assert payload["start_date"] == "2026-08-30"
    info = source_info(item.source)
    assert info.display_name == "nature.com" and info.family == "search"
    storage = Storage(tmp_path / "test.db")
    storage.init()
    assert len(dedup_by_url(items, storage)) == 1
    storage.record_items(items[:1])
    assert dedup_by_url(items, storage) == []
    storage.close()


@pytest.mark.asyncio
@freeze_time("2026-09-06 12:00:00")
async def test_invalid_results_ignored_and_unknown_date_flagged(monkeypatch, source, httpx_mock):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    source["queries"] = {"physics": "one", "earth": "two"}
    httpx_mock.add_response(json={"results": [None, {}, result(url="javascript:bad"), result(title=3), result(published_date="bad")]})
    httpx_mock.add_response(json={"results": [result(published_date="2000-01-01"), result(published_date=None), result(published_date="2099-01-01")]})
    items = await fetch_tavily(source, 168)
    assert len(items) == 1
    assert items[0].raw["publication_date_known"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", 429, 500, 401])
async def test_query_failures_isolated_from_other_queries_and_sources(monkeypatch, source, httpx_mock, failure, caplog):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    source["queries"]["chemistry"] = "second query"
    if failure == "timeout":
        httpx_mock.add_exception(httpx.ReadTimeout("secret-must-not-be-logged"))
    else:
        httpx_mock.add_response(status_code=failure)
    httpx_mock.add_response(json={"results": [result(published_date=None)]})
    monkeypatch.setattr("src.fetchers.fetch_arxiv", AsyncMock(return_value=[]))
    items = await fetch_all([source, {"name": "other", "type": "arxiv"}], 168)
    assert len(items) == 1
    assert "Tavily query physics failed" in caplog.text
    assert "secret-must-not-be-logged" not in caplog.text
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
@freeze_time("2026-09-06 12:00:00")
async def test_per_run_cap_bounds_queries_and_results_without_persistent_state(monkeypatch, source, httpx_mock):
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    source["queries"] = {str(index): f"query {index}" for index in range(20)}
    source["max_results"] = 100
    for _ in range(7):
        httpx_mock.add_response(json={"results": [result()] * 20})
    assert len(await fetch_tavily(source, 168)) == 35
    assert len(httpx_mock.get_requests()) == 7
    source["queries"] = {"physics": "same day rerun"}
    httpx_mock.add_response(json={"results": []})
    assert await fetch_tavily(source, 168) == []
    assert len(httpx_mock.get_requests()) == 8


@pytest.mark.asyncio
async def test_tavily_hint_cannot_override_analyzer(monkeypatch, source, httpx_mock):
    from src.ai4s_analyzer import analyze_item
    from tests.test_ai4s_analyzer import _config
    monkeypatch.setenv("TAVILY_API_KEY", "test-only")
    httpx_mock.add_response(json={"results": [result(published_date=None)]})
    item = (await fetch_tavily(source, 168))[0]
    complete = AsyncMock(return_value=({"is_ai4s": True, "primary_category": "chemistry",
        "secondary_categories": [], "content_type": "paper", "score": 8, "tags": []}, 0))
    monkeypatch.setattr("src.ai4s_analyzer.complete_json", complete)
    monkeypatch.setattr(
        "src.ai4s_analyzer.enrich_item_content",
        AsyncMock(return_value=EnrichedContent(
            item.content, len(item.content), len(item.content)
        )),
    )
    analysis = await analyze_item(item, _config())
    assert analysis.primary_category == "chemistry"
    assert item.raw["retrieval_category_hint"] == "physics"
