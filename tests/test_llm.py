import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.config import Models
from src.llm import (
    LLMError,
    check_api_keys,
    complete_json,
    parse_json_loose,
)


FIX = Path(__file__).parent / "fixtures"


def test_parse_json_loose_plain():
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_parse_json_loose_strips_fenced_codeblock():
    raw = (FIX / "llm_score_response_dirty.txt").read_text(encoding="utf-8")
    out = parse_json_loose(raw)
    assert out["score"] == 8
    assert out["tags"] == ["LLM", "agent"]


def test_parse_json_loose_fixes_trailing_comma():
    assert parse_json_loose('{"a": 1, "b": [1, 2,]}') == {"a": 1, "b": [1, 2]}


def test_parse_json_loose_raises_on_garbage():
    with pytest.raises(LLMError):
        parse_json_loose("not json at all")


def _make_mock_response(text: str, cost: float = 0.001):
    """Build the litellm response shape complete_json reads."""
    response = type("R", (), {})()
    response.choices = [type("C", (), {"message": type("M", (), {"content": text})})()]
    response._hidden_params = {"response_cost": cost}
    return response


@pytest.mark.asyncio
async def test_complete_json_parses_clean_response():
    body = (FIX / "llm_score_response.json").read_text(encoding="utf-8")
    with patch("src.llm.acompletion", new=AsyncMock(return_value=_make_mock_response(body, 0.002))):
        data, cost = await complete_json(
            model="anthropic/claude-haiku-4-5",
            prompt="anything",
            max_tokens=300,
        )
    assert data == {"score": 8, "tags": ["LLM", "agent"]}
    assert cost == pytest.approx(0.002)


@pytest.mark.asyncio
async def test_complete_json_retries_on_bad_json_then_succeeds():
    bad = "this is not json"
    good = (FIX / "llm_score_response.json").read_text(encoding="utf-8")
    mock = AsyncMock(side_effect=[
        _make_mock_response(bad, 0.001),
        _make_mock_response(good, 0.002),
    ])
    with patch("src.llm.acompletion", new=mock):
        data, cost = await complete_json(
            model="anthropic/claude-haiku-4-5",
            prompt="anything",
            max_tokens=300,
        )
    assert data == {"score": 8, "tags": ["LLM", "agent"]}
    assert cost == pytest.approx(0.003)
    assert mock.await_count == 2


@pytest.mark.asyncio
async def test_complete_json_gives_up_after_one_retry():
    bad = "still not json"
    mock = AsyncMock(side_effect=[
        _make_mock_response(bad, 0.001),
        _make_mock_response(bad, 0.001),
    ])
    with patch("src.llm.acompletion", new=mock):
        with pytest.raises(LLMError):
            await complete_json(
                model="anthropic/claude-haiku-4-5",
                prompt="anything",
                max_tokens=300,
            )
    assert mock.await_count == 2


@pytest.mark.asyncio
async def test_complete_json_can_disable_retry_and_reports_failed_cost():
    mock = AsyncMock(return_value=_make_mock_response("not json", 0.004))
    with patch("src.llm.acompletion", new=mock):
        with pytest.raises(LLMError) as error:
            await complete_json(
                model="anthropic/claude-haiku-4-5",
                prompt="anything",
                max_tokens=300,
                max_attempts=1,
            )
    assert mock.await_count == 1
    assert error.value.cost_usd == pytest.approx(0.004)


def test_check_api_keys_anthropic_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    check_api_keys(Models(scorer="anthropic/claude-haiku-4-5",
                          summarizer="anthropic/claude-sonnet-4-6"))


def test_check_api_keys_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        check_api_keys(Models(scorer="anthropic/claude-haiku-4-5",
                              summarizer="anthropic/claude-sonnet-4-6"))


def test_check_api_keys_mixed_providers(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        check_api_keys(Models(scorer="deepseek/deepseek-chat",
                              summarizer="anthropic/claude-sonnet-4-6"))
