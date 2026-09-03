from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ai4s_analyzer import analyze_item, run_ai4s_analyze
from src.config import Config, Models
from src.llm import LLMError
from src.models import Item
from src.storage import Storage


def _item(url: str = "https://example.com/paper") -> Item:
    return Item(
        url=url,
        title=f"AI protein design {url}",
        content="A diffusion model designs proteins and reports experimental results.",
        published_at=datetime.now(timezone.utc),
        source="test",
    )


def _config() -> Config:
    return Config(
        sources=[],
        keywords=["protein design"],
        models=Models(scorer="deepseek/deepseek-chat", summarizer="deepseek/deepseek-chat"),
        score_threshold=7,
    )


def _valid_response(**overrides) -> dict:
    response = {
        "is_ai4s": True,
        "primary_category": "biology",
        "secondary_categories": ["medicine"],
        "content_type": "paper",
        "score": 8,
        "tags": ["protein-design"],
    }
    response.update(overrides)
    return response


@pytest.mark.asyncio
async def test_analyze_item_returns_valid_ai4s_result(monkeypatch):
    complete = AsyncMock(return_value=(_valid_response(), 0.001))
    monkeypatch.setattr("src.ai4s_analyzer.complete_json", complete)

    result = await analyze_item(_item(), _config(), taxonomy="- biology: Biology")

    assert result.is_ai4s is True
    assert result.primary_category == "biology"
    assert result.secondary_categories == ["medicine"]
    assert result.content_type == "paper"
    assert result.score == 8
    assert result.model == "deepseek/deepseek-chat"
    assert result.cost_usd == pytest.approx(0.001)
    prompt = complete.await_args.kwargs["prompt"]
    assert "- biology: Biology" in prompt
    assert "protein design" in prompt
    assert _item().title in prompt


@pytest.mark.asyncio
async def test_analyze_item_accepts_non_ai4s(monkeypatch):
    monkeypatch.setattr(
        "src.ai4s_analyzer.complete_json",
        AsyncMock(return_value=(_valid_response(
            is_ai4s=False,
            primary_category=None,
            secondary_categories=[],
            score=1,
            tags=[],
        ), 0.0)),
    )

    result = await analyze_item(_item(), _config(), taxonomy="taxonomy")

    assert result.is_ai4s is False
    assert result.primary_category is None
    assert result.score == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"primary_category": "computer_science"},
        {"score": 11},
        {"score": -1},
        {"score": True},
        {"secondary_categories": ["medicine", "medicine"]},
        {"secondary_categories": ["biology"]},
        {"secondary_categories": ["medicine", "chemistry", "materials"]},
        {"secondary_categories": ["computer_science"]},
        {"content_type": "blog"},
    ],
)
async def test_analyze_item_rejects_invalid_structured_output(monkeypatch, overrides):
    monkeypatch.setattr(
        "src.ai4s_analyzer.complete_json",
        AsyncMock(return_value=(_valid_response(**overrides), 0.0)),
    )

    with pytest.raises((ValueError, LLMError)):
        await analyze_item(_item(), _config(), taxonomy="taxonomy")


@pytest.mark.asyncio
async def test_analyze_item_rejects_missing_or_invalid_lists(monkeypatch):
    missing = _valid_response()
    missing.pop("tags")
    complete = AsyncMock(side_effect=[
        (missing, 0.0),
        (_valid_response(secondary_categories="medicine"), 0.0),
        (_valid_response(tags="protein-design"), 0.0),
    ])
    monkeypatch.setattr("src.ai4s_analyzer.complete_json", complete)

    for _ in range(3):
        with pytest.raises(LLMError):
            await analyze_item(_item(), _config(), taxonomy="taxonomy")


@pytest.mark.asyncio
async def test_batch_isolates_failure_and_persists_success(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item("https://success"), _item("https://failure")])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    async def fake_complete_json(*, model, prompt, max_tokens, temperature=0.2):
        if "https://failure" in prompt:
            raise LLMError("simulated failure")
        return _valid_response(), 0.001

    monkeypatch.setattr("src.ai4s_analyzer.complete_json", fake_complete_json)

    metrics = await run_ai4s_analyze(storage, _config())

    assert metrics == {
        "analyzed": 1,
        "ai4s_true": 1,
        "ai4s_false": 0,
        "passed_threshold": 1,
        "errors": 1,
        "cost_usd": pytest.approx(0.001),
    }
    assert storage.get_ai4s_analysis("https://success") is not None
    assert storage.get_ai4s_analysis("https://failure") is None
    storage.close()


@pytest.mark.asyncio
async def test_batch_limit_caps_calls_before_analysis(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    storage.record_items([_item(f"https://{index}") for index in range(3)])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock(return_value=(_valid_response(), 0.001))
    monkeypatch.setattr("src.ai4s_analyzer.complete_json", complete)

    metrics = await run_ai4s_analyze(storage, _config(), limit=2)

    assert metrics["analyzed"] == 2
    assert complete.await_count == 2
    remaining = storage.get_unanalyzed_items(within_days=7)
    assert len(remaining) == 1
    storage.close()
