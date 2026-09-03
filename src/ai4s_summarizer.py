import asyncio
import logging

from src.config import Config
from src.llm import LLMError, check_api_keys, complete_json
from src.models import AI4SAnalysis, AI4SSummary
from src.prompts import load_prompt, render
from src.storage import Storage


logger = logging.getLogger(__name__)

_SUMMARY_CONTENT_CHARS = 4000
_SUMMARY_FIELDS = (
    "scientific_problem",
    "ai_method",
    "main_result",
    "innovation",
    "scientific_significance",
    "resources",
)
_UNSUPPORTED_INFERENCE_MARKERS = ("可推断", "推测")


def _render_summary_prompt(analysis: AI4SAnalysis, keywords: list[str]) -> str:
    item = analysis.item
    analyzer = analysis.analyzer
    content = (item.content or "")[:_SUMMARY_CONTENT_CHARS]
    if not content.strip():
        content = "（原始正文为空；除标题直接陈述的事实外，所有字段均应明确写信息不足。）"
    return render(load_prompt("summarize_ai4s"), {
        "keywords": ", ".join(keywords) or "(none)",
        "source": item.source,
        "date": item.published_at.date().isoformat(),
        "url": item.url,
        "title": item.title,
        "primary_category": analyzer.primary_category,
        "secondary_categories": ", ".join(analyzer.secondary_categories) or "(none)",
        "content_type": analyzer.content_type,
        "score": analyzer.score,
        "tags": ", ".join(analyzer.tags) or "(none)",
        "content": content,
    })


async def summarize_analysis(
    analysis: AI4SAnalysis,
    cfg: Config,
) -> AI4SSummary:
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    data, cost = await complete_json(
        model=cfg.models.summarizer,
        prompt=_render_summary_prompt(analysis, cfg.keywords),
        max_tokens=1500,
    )
    missing = [field for field in _SUMMARY_FIELDS if field not in data]
    if missing:
        raise LLMError(f"AI4S summary missing fields: {missing}")
    wrong_types = [
        field for field in _SUMMARY_FIELDS if not isinstance(data[field], str)
    ]
    if wrong_types:
        raise LLMError(f"AI4S summary fields must be strings: {wrong_types}")
    inferred = [
        field
        for field in _SUMMARY_FIELDS
        if any(marker in data[field] for marker in _UNSUPPORTED_INFERENCE_MARKERS)
    ]
    if inferred:
        raise LLMError(f"AI4S summary contains unsupported inference: {inferred}")
    return AI4SSummary(
        scientific_problem=data["scientific_problem"],
        ai_method=data["ai_method"],
        main_result=data["main_result"],
        innovation=data["innovation"],
        scientific_significance=data["scientific_significance"],
        resources=data["resources"],
        model=cfg.models.summarizer,
        cost_usd=cost,
    )


async def _summarize_one(
    analysis: AI4SAnalysis,
    cfg: Config,
) -> tuple[AI4SAnalysis, AI4SSummary | None, Exception | None]:
    try:
        return analysis, await summarize_analysis(analysis, cfg), None
    except Exception as error:
        logger.warning("AI4S summary failed for %s: %s", analysis.item.url, error)
        return analysis, None, error


async def run_ai4s_summarize(
    storage: Storage,
    cfg: Config,
    limit: int | None = None,
) -> dict[str, int | float]:
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")
    check_api_keys(cfg.models)

    candidates = storage.get_unsummarized_ai4s_analyses(
        min_score=cfg.score_threshold,
    )
    batch_limit = cfg.top_n if limit is None else min(cfg.top_n, limit)
    selected = candidates[:batch_limit]
    logger.info(
        "AI4S summary candidates=%d selected=%d",
        len(candidates),
        len(selected),
    )

    metrics: dict[str, int | float] = {
        "candidates": len(candidates),
        "selected": len(selected),
        "summarized": 0,
        "errors": 0,
        "cost_usd": 0.0,
    }
    results = await asyncio.gather(
        *(_summarize_one(analysis, cfg) for analysis in selected)
    )
    for analysis, summary, error in results:
        if error is not None or summary is None:
            metrics["errors"] += 1
            continue
        storage.save_ai4s_summary(analysis.item.url, summary)
        metrics["summarized"] += 1
        metrics["cost_usd"] += summary.cost_usd

    logger.info(
        "AI4S summarize done: candidates=%d selected=%d summarized=%d "
        "errors=%d cost=$%.6f",
        metrics["candidates"],
        metrics["selected"],
        metrics["summarized"],
        metrics["errors"],
        metrics["cost_usd"],
    )
    return metrics
