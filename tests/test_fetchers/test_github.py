import json
import re

import pytest
from freezegun import freeze_time

from src.fetchers.github import fetch_github


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_github_filters_by_pushed_recency_and_stars(
    httpx_mock, github_search_json
):
    httpx_mock.add_response(
        url=re.compile(r"https://api\.github\.com/search/repositories.*"),
        text=github_search_json,
        headers={"content-type": "application/json"},
    )
    source = {
        "name": "github-trending-agent",
        "type": "github",
        "topic": "agent",
        "min_stars": 10,
    }
    items = await fetch_github(source, window_hours=72)
    urls = [i.url for i in items]
    assert "https://github.com/alice/cool-agent" in urls
    # bob/old-agent has min_stars=10 below 5? It's 5, so below threshold AND old
    assert "https://github.com/bob/old-agent" not in urls
    cool = next(i for i in items if i.url == "https://github.com/alice/cool-agent")
    assert cool.title == "alice/cool-agent"
    assert cool.content == (
        "A cool LLM agent framework\n\n"
        "Topics: agent, llm\n\n"
        "Language: Python"
    )
    assert cool.source == "github:github-trending-agent"
    assert cool.raw == json.loads(github_search_json)["items"][0]


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_github_handles_empty_topics(httpx_mock):
    repo = {
        "full_name": "alice/science-tool",
        "html_url": "https://github.com/alice/science-tool",
        "description": "A scientific tool",
        "stargazers_count": 50,
        "language": "Python",
        "topics": [],
        "homepage": "https://science-tool.example",
        "pushed_at": "2026-05-14T15:00:00Z",
    }
    httpx_mock.add_response(
        url=re.compile(r"https://api\.github\.com/search/repositories.*"),
        json={"items": [repo]},
    )
    source = {
        "name": "github-science",
        "type": "github",
        "topic": "science",
        "min_stars": 10,
    }

    items = await fetch_github(source, window_hours=72)

    assert len(items) == 1
    assert items[0].content == (
        "A scientific tool\n\n"
        "Language: Python\n\n"
        "Homepage: https://science-tool.example"
    )
    assert "Topics:" not in items[0].content
    assert items[0].url == repo["html_url"]
    assert items[0].title == repo["full_name"]
    assert items[0].source == "github:github-science"
    assert items[0].raw == repo


@pytest.mark.asyncio
@freeze_time("2026-05-15 12:00:00")
async def test_fetch_github_limits_topic_metadata(httpx_mock):
    topics = [f"topic-{index}" for index in range(10)]
    repo = {
        "full_name": "alice/science-tool",
        "html_url": "https://github.com/alice/science-tool",
        "description": "A scientific tool",
        "stargazers_count": 50,
        "topics": topics,
        "pushed_at": "2026-05-14T15:00:00Z",
    }
    httpx_mock.add_response(
        url=re.compile(r"https://api\.github\.com/search/repositories.*"),
        json={"items": [repo]},
    )
    source = {
        "name": "github-science",
        "type": "github",
        "topic": "science",
        "min_stars": 10,
    }

    items = await fetch_github(source, window_hours=72)

    assert ", ".join(topics[:8]) in items[0].content
    assert topics[8] not in items[0].content
