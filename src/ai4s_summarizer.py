import asyncio
import logging
import re

from src.config import Config
from src.content_enrichment import enrich_item_content
from src.llm import LLMError, check_api_keys, complete_json
from src.models import AI4SAnalysis, AI4SSummary
from src.prompts import load_prompt, render
from src.storage import Storage


logger = logging.getLogger(__name__)

_SUMMARY_CONTENT_CHARS = 9000
_SUMMARY_PREFIX_CHARS = 4200
_CONTENT_CHUNK_CHARS = 900
_RESULT_MARKERS = (
    "result",
    "performance",
    "benchmark",
    "experiment",
    "dataset",
    "evaluation",
    "accuracy",
    "improvement",
    "compared",
    "we propose",
    "we show",
    "结果",
    "性能",
    "实验",
    "数据集",
    "评测",
    "准确率",
    "提升",
    "对比",
)
_FACTUAL_SUMMARY_FIELDS = (
    "scientific_problem",
    "ai_method",
    "main_result",
    "innovation",
    "scientific_significance",
    "resources",
)
_SUMMARY_FIELDS = _FACTUAL_SUMMARY_FIELDS + ("assessment",)
# Hard limits allow modest variance beyond the prompt's concise targets.
# Count Chinese characters, allowing method names and measured values in English.
_SUMMARY_CHAR_LIMITS = {"scientific_problem": 100, "ai_method": 140, "main_result": 140,
                        "innovation": 100, "scientific_significance": 100, "assessment": 100}
_UNSUPPORTED_INFERENCE_MARKERS = ("可推断", "推测")


def _content_chunks(content: str) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    chunks: list[str] = []
    for paragraph in paragraphs:
        chunks.extend(
            paragraph[index:index + _CONTENT_CHUNK_CHARS]
            for index in range(0, len(paragraph), _CONTENT_CHUNK_CHARS)
        )
    return chunks


def select_summary_content(content: str) -> str:
    text = content.strip()
    if len(text) <= _SUMMARY_CONTENT_CHARS:
        return text

    chunks = _content_chunks(text)
    selected: set[int] = set()
    used_chars = 0
    for index, chunk in enumerate(chunks):
        if used_chars >= _SUMMARY_PREFIX_CHARS:
            break
        selected.add(index)
        used_chars += len(chunk) + 2

    for index, chunk in enumerate(chunks):
        if index in selected or not any(marker in chunk.casefold() for marker in _RESULT_MARKERS):
            continue
        if used_chars + len(chunk) + 2 > _SUMMARY_CONTENT_CHARS:
            continue
        selected.add(index)
        used_chars += len(chunk) + 2

    for index, chunk in enumerate(chunks):
        if index in selected:
            continue
        if used_chars + len(chunk) + 2 > _SUMMARY_CONTENT_CHARS:
            break
        selected.add(index)
        used_chars += len(chunk) + 2

    return "\n\n".join(chunks[index] for index in sorted(selected))[:_SUMMARY_CONTENT_CHARS]


def _render_summary_prompt(
    analysis: AI4SAnalysis,
    keywords: list[str],
    content: str | None = None,
) -> str:
    item = analysis.item
    analyzer = analysis.analyzer
    selected_content = select_summary_content(item.content if content is None else content)
    if not selected_content:
        selected_content = "（原始正文为空；除标题直接陈述的事实外，所有字段均应明确写信息不足。）"
    return render(load_prompt("summarize_ai4s"), {
        "keywords": ", ".join(keywords) or "(none)",
        "source": item.source,
        "date": ("发布时间未知" if item.raw.get("publication_date_known") is False
                 else item.published_at.date().isoformat()),
        "url": item.url,
        "title": item.title,
        "primary_category": analyzer.primary_category,
        "secondary_categories": ", ".join(analyzer.secondary_categories) or "(none)",
        "content_type": analyzer.content_type,
        "score": analyzer.score,
        "tags": ", ".join(analyzer.tags) or "(none)",
        "content": selected_content,
    })


async def summarize_analysis(
    analysis: AI4SAnalysis,
    cfg: Config,
) -> AI4SSummary:
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    enriched = await enrich_item_content(analysis.item)
    data, cost = await complete_json(
        model=cfg.models.summarizer,
        prompt=_render_summary_prompt(analysis, cfg.keywords, enriched.text),
        max_tokens=1500,
    )
    missing = [field for field in _SUMMARY_FIELDS if field not in data]
    if missing:
        raise LLMError(f"AI4S summary missing fields: {missing}", cost_usd=cost)
    wrong_types = [
        field for field in _SUMMARY_FIELDS if not isinstance(data[field], str)
    ]
    if wrong_types:
        raise LLMError(f"AI4S summary fields must be strings: {wrong_types}", cost_usd=cost)
    if not data["assessment"].strip():
        raise LLMError("AI4S summary assessment must not be empty", cost_usd=cost)
    normalized_assessment = " ".join(data["assessment"].split()).rstrip("。.!！")
    if any(normalized_assessment == " ".join(data[field].split()).rstrip("。.!！")
           for field in _FACTUAL_SUMMARY_FIELDS[:-1]):
        raise LLMError("AI4S summary assessment must add judgement beyond factual summary", cost_usd=cost)
    too_long = [field for field, limit in _SUMMARY_CHAR_LIMITS.items()
                if len(re.findall(r"[\u3400-\u9fff]", data[field])) > limit
                or len(data[field]) > limit * 4]
    if too_long:
        # Reject, never truncate evidence or silently issue another paid request.
        raise LLMError(f"AI4S summary exceeds concise length limits: {too_long}", cost_usd=cost)
    inferred = [
        field
        for field in _FACTUAL_SUMMARY_FIELDS
        if any(marker in data[field] for marker in _UNSUPPORTED_INFERENCE_MARKERS)
    ]
    if inferred:
        raise LLMError(f"AI4S summary contains unsupported inference: {inferred}", cost_usd=cost)
    summary = AI4SSummary(
        scientific_problem=data["scientific_problem"],
        ai_method=data["ai_method"],
        main_result=data["main_result"],
        innovation=data["innovation"],
        scientific_significance=data["scientific_significance"],
        resources=data["resources"],
        model=cfg.models.summarizer,
        cost_usd=cost,
        assessment=data["assessment"],
    )
    summary_chars = sum(len(data[field]) for field in _FACTUAL_SUMMARY_FIELDS[:-1])
    logger.info(
        "AI4S reading: title=%s original_chars=%d enriched_chars=%d "
        "summary_chars=%d assessment_chars=%d enrichment=%s",
        analysis.item.title,
        enriched.original_chars,
        enriched.enriched_chars,
        summary_chars,
        len(summary.assessment or ""),
        enriched.method or "none",
    )
    return summary


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
            metrics["cost_usd"] += float(getattr(error, "cost_usd", 0.0))
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
