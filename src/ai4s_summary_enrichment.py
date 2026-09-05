"""One-time, evidence-bound enrichment for recent legacy AI4S summaries."""

import asyncio
import json
import logging
import re

from src.ai4s_summarizer import select_summary_content
from src.config import Config
from src.content_enrichment import enrich_item_content
from src.information_sufficiency import (
    REQUIRED_INFORMATION_FIELDS,
    has_substantive_assessment,
    has_sufficient_information,
    is_substantive,
)
from src.llm import LLMError, check_api_keys, complete_json
from src.models import AI4SAnalysis
from src.prompts import load_prompt, render
from src.storage import Storage


logger = logging.getLogger(__name__)

MAX_ENRICHMENT_ITEMS = 30
_ENRICHABLE_FIELDS = REQUIRED_INFORMATION_FIELDS + ("main_result",)
_OUTPUT_FIELDS = _ENRICHABLE_FIELDS + ("assessment",)
_UNSUPPORTED_INFERENCE_MARKERS = ("可推断", "推测")


def select_enrichment_candidates(
    candidates: list[AI4SAnalysis], limit: int
) -> list[AI4SAnalysis]:
    unassessed = [item for item in candidates if not has_substantive_assessment(item)]
    return sorted(
        unassessed,
        key=lambda item: (
            sum(
                not is_substantive(getattr(item.summary, field))
                for field in REQUIRED_INFORMATION_FIELDS
            ),
            -len(item.item.content.strip()),
            -item.analyzer.score,
            -item.item.published_at.timestamp(),
        ),
    )[:limit]


def _render_enrichment_prompt(analysis: AI4SAnalysis, content: str) -> str:
    assert analysis.summary is not None
    selected_content = select_summary_content(content)
    if not selected_content:
        selected_content = "（正文为空；不得凭标题之外的信息补造事实。）"
    existing = {
        field: getattr(analysis.summary, field) for field in _ENRICHABLE_FIELDS
    }
    return render(load_prompt("enrich_ai4s_summary"), {
        "title": analysis.item.title,
        "url": analysis.item.url,
        "source": analysis.item.source,
        "category": analysis.analyzer.primary_category,
        "score": analysis.analyzer.score,
        "tags": ", ".join(analysis.analyzer.tags) or "(none)",
        "existing_summary": json.dumps(existing, ensure_ascii=False, indent=2),
        "content": selected_content,
    })


def _validate_response(data: dict, analysis: AI4SAnalysis) -> dict[str, str]:
    missing = [field for field in _OUTPUT_FIELDS if field not in data]
    if missing:
        raise LLMError(f"AI4S enrichment missing fields: {missing}")
    wrong_types = [field for field in _OUTPUT_FIELDS if not isinstance(data[field], str)]
    if wrong_types:
        raise LLMError(f"AI4S enrichment fields must be strings: {wrong_types}")

    assessment = data["assessment"].strip()
    sentences = [part.strip() for part in re.split(r"[。！？!?]+", assessment) if part.strip()]
    if not is_substantive(assessment) or len(assessment) < 40 or not 2 <= len(sentences) <= 3:
        raise LLMError("AI4S enrichment assessment must contain 2-3 substantive sentences")
    innovation = data["innovation"].strip()
    if innovation and " ".join(assessment.split()).rstrip("。.!！") == " ".join(
        innovation.split()
    ).rstrip("。.!！"):
        raise LLMError("AI4S enrichment assessment must add judgement beyond innovation")

    assert analysis.summary is not None
    inferred = [
        field
        for field in _ENRICHABLE_FIELDS
        if not is_substantive(getattr(analysis.summary, field))
        and any(marker in data[field] for marker in _UNSUPPORTED_INFERENCE_MARKERS)
    ]
    if inferred:
        raise LLMError(f"AI4S enrichment contains unsupported inference: {inferred}")
    return {field: data[field].strip() for field in _OUTPUT_FIELDS}


async def enrich_analysis(
    analysis: AI4SAnalysis, cfg: Config
) -> tuple[dict[str, str], float]:
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    enriched = await enrich_item_content(analysis.item)
    data, cost = await complete_json(
        model=cfg.models.summarizer,
        prompt=_render_enrichment_prompt(analysis, enriched.text),
        max_tokens=1300,
        max_attempts=1,
    )
    return _validate_response(data, analysis), cost


async def _enrich_one(
    analysis: AI4SAnalysis, cfg: Config
) -> tuple[AI4SAnalysis, dict[str, str] | None, float, Exception | None]:
    try:
        data, cost = await enrich_analysis(analysis, cfg)
        return analysis, data, cost, None
    except Exception as error:
        logger.warning("AI4S summary enrichment failed for %s: %s", analysis.item.url, error)
        return analysis, None, float(getattr(error, "cost_usd", 0.0)), error


async def run_ai4s_summary_enrichment(
    storage: Storage,
    cfg: Config,
    *,
    limit: int,
    within_days: int = 7,
) -> dict[str, int | float]:
    if not 1 <= limit <= MAX_ENRICHMENT_ITEMS:
        raise ValueError(f"limit must be between 1 and {MAX_ENRICHMENT_ITEMS}")
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.summarizer")
    check_api_keys(cfg.models)

    recent = storage.get_recent_summarized_ai4s_analyses(
        min_score=cfg.score_threshold, within_days=within_days
    )
    candidates = [item for item in recent if not has_substantive_assessment(item)]
    selected = select_enrichment_candidates(recent, limit)
    metrics: dict[str, int | float] = {
        "candidates": len(candidates),
        "selected": len(selected),
        "enriched": 0,
        "qualified_after": 0,
        "errors": 0,
        "cost_usd": 0.0,
    }
    logger.info(
        "AI4S enrichment candidates=%d selected=%d limit=%d",
        len(candidates), len(selected), limit,
    )

    results = await asyncio.gather(*(_enrich_one(item, cfg) for item in selected))
    for analysis, data, cost, error in results:
        metrics["cost_usd"] += cost
        if error is not None or data is None:
            metrics["errors"] += 1
            continue
        assert analysis.summary is not None
        merged = {
            field: (
                getattr(analysis.summary, field)
                if is_substantive(getattr(analysis.summary, field))
                else data[field]
            )
            for field in _ENRICHABLE_FIELDS
        }
        storage.save_ai4s_summary_enrichment(
            analysis.item.url,
            scientific_problem=merged["scientific_problem"],
            ai_method=merged["ai_method"],
            main_result=merged["main_result"],
            innovation=merged["innovation"],
            scientific_significance=merged["scientific_significance"],
            assessment=data["assessment"],
            model=cfg.models.summarizer,
            cost_usd=cost,
        )
        metrics["enriched"] += 1
        updated = storage.get_ai4s_analysis(analysis.item.url)
        if updated is not None and has_sufficient_information(updated):
            metrics["qualified_after"] += 1

    logger.info(
        "AI4S enrichment done: candidates=%d selected=%d enriched=%d "
        "qualified_after=%d errors=%d cost=$%.6f",
        metrics["candidates"], metrics["selected"], metrics["enriched"],
        metrics["qualified_after"], metrics["errors"], metrics["cost_usd"],
    )
    return metrics
