from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from src.fetchers.rss import choose_best_content, fetch_rss


def test_choose_best_content_prefers_entry_content():
    entry = {
        "content": [{"value": "<p>Full <strong>research</strong> text.</p>"}],
        "summary": "Short summary.",
        "description": "Short description.",
    }

    assert choose_best_content(entry) == "Full research text."


def test_choose_best_content_falls_back_to_summary():
    assert choose_best_content({"summary": "Useful summary."}) == "Useful summary."


def test_choose_best_content_falls_back_to_description():
    assert choose_best_content({"description": "Useful description."}) == "Useful description."


def test_choose_best_content_returns_empty_string_without_content():
    assert choose_best_content({}) == ""


def test_choose_best_content_does_not_duplicate_identical_content():
    lead = (
        "Agentic systems can coordinate complex scientific work across long "
        "research workflows."
    )
    entry = {
        "content": [{"value": f"{lead}... {lead} New details follow."}],
        "summary": f"{lead}...",
    }

    assert choose_best_content(entry) == f"{lead} New details follow."


def test_choose_best_content_cleans_basic_html():
    entry = {
        "content": [
            {
                "value": (
                    "<p>Alpha&nbsp;<strong>beta</strong> "
                    "<a href='https://example.com'>study (opens in new tab)</a></p>"
                    "<script>bad()</script>"
                )
            }
        ]
    }

    assert choose_best_content(entry) == "Alpha beta study"


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_rss_returns_items_within_window(httpx_mock, rss_feed_xml):
    httpx_mock.add_response(
        url="https://example.com/feed",
        text=rss_feed_xml,
        headers={"content-type": "application/rss+xml"},
    )
    source = {
        "name": "example",
        "type": "rss",
        "url": "https://example.com/feed",
    }
    items = await fetch_rss(source, window_hours=36)
    assert len(items) == 1
    assert items[0].url == "https://example.com/blog/agents"
    assert items[0].title == "Recent Post About Agents"
    assert items[0].content == "This is about LLM agents."
    assert items[0].source == "rss:example"
    assert items[0].published_at == datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_rss_filters_old_items(httpx_mock, rss_feed_xml):
    httpx_mock.add_response(
        url="https://example.com/feed",
        text=rss_feed_xml,
    )
    source = {
        "name": "example",
        "type": "rss",
        "url": "https://example.com/feed",
    }
    items = await fetch_rss(source, window_hours=36)
    # "Old Post" is from 2024, must be filtered.
    urls = [i.url for i in items]
    assert "https://example.com/blog/old" not in urls


@pytest.mark.asyncio
async def test_fetch_rss_http_error_raises(httpx_mock):
    httpx_mock.add_response(
        url="https://example.com/feed",
        status_code=500,
    )
    source = {
        "name": "example",
        "type": "rss",
        "url": "https://example.com/feed",
    }
    with pytest.raises(Exception):
        await fetch_rss(source, window_hours=36)
