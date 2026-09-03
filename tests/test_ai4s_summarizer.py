from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.ai4s_summarizer import run_ai4s_summarize, summarize_analysis
from src.config import Config, Models
from src.llm import LLMError
from src.models import AI4SAnalysis, AI4SSummary, AnalyzerResult, Item
from src.storage import Storage


def _config(*, threshold: int = 7, top_n: int = 10) -> Config:
    return Config(
        sources=[],
        keywords=["AI for Science", "protein design"],
        models=Models(
            scorer="deepseek/deepseek-chat",
            summarizer="deepseek/deepseek-chat",
        ),
        score_threshold=threshold,
        top_n=top_n,
    )


def _item(url: str, *, hours_ago: int = 0) -> Item:
    return Item(
        url=url,
        title=f"AI4S item {url}",
        content="A model is used for a scientific task and reports measured results.",
        published_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
        source="test",
    )


def _result(*, score: int = 8, is_ai4s: bool = True) -> AnalyzerResult:
    return AnalyzerResult(
        is_ai4s=is_ai4s,
        primary_category="biology" if is_ai4s else None,
        secondary_categories=[],
        content_type="paper",
        score=score,
        tags=["protein-design"] if is_ai4s else [],
        model="test-analyzer",
        cost_usd=0.001,
    )


def _summary_response(**overrides) -> dict:
    response = {
        "scientific_problem": "预测蛋白质结构。",
        "ai_method": "使用 Transformer 建模序列与结构关系。",
        "main_result": "原文未披露明确量化结果。",
        "innovation": "联合建模蛋白质序列与结构。",
        "scientific_significance": "原文说明该方法减少实验筛选工作量。",
        "resources": "未提供额外科研资源。",
    }
    response.update(overrides)
    return response


def _summary() -> AI4SSummary:
    return AI4SSummary(
        **_summary_response(),
        model="test-summarizer",
        cost_usd=0.002,
    )


def _analysis(content: str = "scientific content") -> AI4SAnalysis:
    item = _item("https://example.com/paper")
    item.content = content
    return AI4SAnalysis(item=item, analyzer=_result())


def _store_analysis(
    storage: Storage,
    url: str,
    *,
    score: int = 8,
    is_ai4s: bool = True,
    hours_ago: int = 0,
) -> None:
    item = _item(url, hours_ago=hours_ago)
    storage.record_items([item])
    storage.save_analyzer_result(url, _result(score=score, is_ai4s=is_ai4s))


@pytest.mark.asyncio
async def test_summarize_analysis_returns_valid_summary(monkeypatch):
    complete = AsyncMock(return_value=(_summary_response(), 0.002))
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)
    analysis = _analysis("x" * 4000 + "SHOULD_NOT_APPEAR")

    summary = await summarize_analysis(analysis, _config())

    assert summary.scientific_problem == "预测蛋白质结构。"
    assert summary.ai_method.startswith("使用 Transformer")
    assert summary.model == "deepseek/deepseek-chat"
    assert summary.cost_usd == pytest.approx(0.002)
    prompt = complete.await_args.kwargs["prompt"]
    assert "biology" in prompt
    assert "protein-design" in prompt
    assert analysis.item.title in prompt
    assert "SHOULD_NOT_APPEAR" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", list(_summary_response()))
async def test_summarize_analysis_rejects_missing_fields(monkeypatch, missing_field):
    response = _summary_response()
    response.pop(missing_field)
    monkeypatch.setattr(
        "src.ai4s_summarizer.complete_json",
        AsyncMock(return_value=(response, 0.0)),
    )

    with pytest.raises(LLMError, match="missing fields"):
        await summarize_analysis(_analysis(), _config())


@pytest.mark.asyncio
async def test_summarize_analysis_rejects_wrong_field_type(monkeypatch):
    monkeypatch.setattr(
        "src.ai4s_summarizer.complete_json",
        AsyncMock(return_value=(_summary_response(main_result=123), 0.0)),
    )

    with pytest.raises(LLMError, match="must be strings"):
        await summarize_analysis(_analysis(), _config())


@pytest.mark.asyncio
async def test_summarize_analysis_rejects_unsupported_inference(monkeypatch):
    monkeypatch.setattr(
        "src.ai4s_summarizer.complete_json",
        AsyncMock(return_value=(_summary_response(
            scientific_significance="可推断该方法可能提高科研效率。",
        ), 0.0)),
    )

    with pytest.raises(LLMError, match="unsupported inference"):
        await summarize_analysis(_analysis(), _config())


@pytest.mark.asyncio
async def test_empty_content_is_explicitly_marked_as_insufficient(monkeypatch):
    complete = AsyncMock(return_value=(_summary_response(), 0.0))
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)

    await summarize_analysis(_analysis(""), _config())

    prompt = complete.await_args.kwargs["prompt"]
    assert "原始正文为空" in prompt


@pytest.mark.asyncio
async def test_batch_excludes_non_ai4s_low_score_and_already_summarized(
    monkeypatch,
    tmp_path: Path,
):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    _store_analysis(storage, "https://non-ai4s", score=1, is_ai4s=False)
    _store_analysis(storage, "https://low-score", score=5)
    _store_analysis(storage, "https://already-summarized", score=9)
    storage.save_ai4s_summary("https://already-summarized", _summary())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock()
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)

    metrics = await run_ai4s_summarize(storage, _config())

    assert metrics["candidates"] == 0
    assert metrics["selected"] == 0
    assert complete.await_count == 0
    storage.close()


@pytest.mark.asyncio
async def test_batch_uses_score_order_and_smaller_limit(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    _store_analysis(storage, "https://score-7", score=7)
    _store_analysis(storage, "https://score-9", score=9)
    _store_analysis(storage, "https://score-8", score=8)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock(return_value=(_summary_response(), 0.002))
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)

    metrics = await run_ai4s_summarize(storage, _config(top_n=2), limit=1)

    assert metrics["candidates"] == 3
    assert metrics["selected"] == 1
    assert metrics["summarized"] == 1
    assert complete.await_count == 1
    assert storage.get_ai4s_analysis("https://score-9").summary is not None
    assert storage.get_ai4s_analysis("https://score-8").summary is None
    storage.close()


@pytest.mark.asyncio
async def test_batch_isolates_failure_and_leaves_it_unsummarized(
    monkeypatch,
    tmp_path: Path,
):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    _store_analysis(storage, "https://success", score=8)
    _store_analysis(storage, "https://failure", score=8)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    async def fake_complete_json(*, model, prompt, max_tokens, temperature=0.2):
        if "https://failure" in prompt:
            raise LLMError("simulated failure")
        return _summary_response(), 0.002

    monkeypatch.setattr("src.ai4s_summarizer.complete_json", fake_complete_json)

    metrics = await run_ai4s_summarize(storage, _config())

    assert metrics["summarized"] == 1
    assert metrics["errors"] == 1
    assert metrics["cost_usd"] == pytest.approx(0.002)
    assert storage.get_ai4s_analysis("https://success").summary is not None
    assert storage.get_ai4s_analysis("https://failure").summary is None
    storage.close()


@pytest.mark.asyncio
async def test_second_run_does_not_repeat_completed_summary(monkeypatch, tmp_path: Path):
    storage = Storage(tmp_path / "test.db")
    storage.init()
    _store_analysis(storage, "https://done", score=9)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    complete = AsyncMock(return_value=(_summary_response(), 0.002))
    monkeypatch.setattr("src.ai4s_summarizer.complete_json", complete)

    first = await run_ai4s_summarize(storage, _config(), limit=1)
    second = await run_ai4s_summarize(storage, _config(), limit=1)

    assert first["summarized"] == 1
    assert second["candidates"] == 0
    assert second["summarized"] == 0
    assert complete.await_count == 1
    storage.close()
