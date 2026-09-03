import asyncio
import logging
from pathlib import Path

import yaml

from src.config import Config
from src.llm import LLMError, check_api_keys, complete_json
from src.models import AI4S_CATEGORY_IDS, AnalyzerResult, Item
from src.prompts import load_prompt, render
from src.storage import Storage


logger = logging.getLogger(__name__)

_ANALYZER_CONTENT_CHARS = 1200
_REQUIRED_FIELDS = (
    "is_ai4s",
    "primary_category",
    "secondary_categories",
    "content_type",
    "score",
    "tags",
)


def _load_taxonomy(path: Path = Path("config/categories.yaml")) -> str:
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    categories = document.get("categories")
    if not isinstance(categories, dict) or set(categories) != AI4S_CATEGORY_IDS:
        raise ValueError("categories.yaml must define the seven AI4S category IDs")
    return "\n".join(
        f"- {category_id}: {category['name_en']} — {category['description']}"
        for category_id, category in categories.items()
    )


def _render_analyzer_prompt(
    item: Item,
    keywords: list[str],
    taxonomy: str,
) -> str:
    return render(load_prompt("analyze_ai4s"), {
        "taxonomy": taxonomy,
        "keywords": ", ".join(keywords) or "(none)",
        "source": item.source,
        "date": item.published_at.date().isoformat(),
        "title": item.title,
        "content": (item.content or "")[:_ANALYZER_CONTENT_CHARS],
    })


async def analyze_item(
    item: Item,
    cfg: Config,
    taxonomy: str | None = None,
) -> AnalyzerResult:
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.scorer to run analyze")
    data, cost = await complete_json(
        model=cfg.models.scorer,
        prompt=_render_analyzer_prompt(
            item,
            cfg.keywords,
            taxonomy if taxonomy is not None else _load_taxonomy(),
        ),
        max_tokens=400,
    )
    missing = [field for field in _REQUIRED_FIELDS if field not in data]
    if missing:
        raise LLMError(f"analyzer missing fields: {missing}")

    secondary_categories = data["secondary_categories"]
    tags = data["tags"]
    if not isinstance(secondary_categories, list):
        raise LLMError("secondary_categories must be a list")
    if not all(isinstance(category, str) for category in secondary_categories):
        raise LLMError("secondary_categories must contain only strings")
    if not isinstance(tags, list):
        raise LLMError("tags must be a list")
    if len(tags) > 4 or not all(isinstance(tag, str) and tag for tag in tags):
        raise LLMError("tags must contain at most four non-empty strings")

    return AnalyzerResult(
        is_ai4s=data["is_ai4s"],
        primary_category=data["primary_category"],
        secondary_categories=secondary_categories,
        content_type=data["content_type"],
        score=data["score"],
        tags=tags,
        model=cfg.models.scorer,
        cost_usd=cost,
    )


async def _analyze_one(
    item: Item,
    cfg: Config,
    taxonomy: str,
) -> tuple[Item, AnalyzerResult | None, Exception | None]:
    try:
        return item, await analyze_item(item, cfg, taxonomy), None
    except Exception as error:
        logger.warning("AI4S analysis failed for %s: %s", item.url, error)
        return item, None, error


async def run_ai4s_analyze(
    storage: Storage,
    cfg: Config,
    limit: int | None = None,
    categories_path: Path = Path("config/categories.yaml"),
) -> dict[str, int | float]:
    if cfg.models is None:
        raise RuntimeError("preferences.yaml must define models.scorer to run analyze")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")
    check_api_keys(cfg.models)

    taxonomy = _load_taxonomy(categories_path)
    items = storage.get_unanalyzed_items(within_days=7)
    if limit is not None:
        items = items[:limit]
    logger.info("found %d unanalyzed items in the last 7 days", len(items))

    metrics: dict[str, int | float] = {
        "analyzed": 0,
        "ai4s_true": 0,
        "ai4s_false": 0,
        "passed_threshold": 0,
        "errors": 0,
        "cost_usd": 0.0,
    }
    results = await asyncio.gather(
        *(_analyze_one(item, cfg, taxonomy) for item in items)
    )
    for item, result, error in results:
        if error is not None or result is None:
            metrics["errors"] += 1
            continue
        storage.save_analyzer_result(item.url, result)
        metrics["analyzed"] += 1
        metrics["cost_usd"] += result.cost_usd
        if result.is_ai4s:
            metrics["ai4s_true"] += 1
            if result.score >= cfg.score_threshold:
                metrics["passed_threshold"] += 1
        else:
            metrics["ai4s_false"] += 1

    logger.info(
        "AI4S analyze done: analyzed=%d ai4s_true=%d ai4s_false=%d "
        "passed_threshold=%d errors=%d cost=$%.6f",
        metrics["analyzed"],
        metrics["ai4s_true"],
        metrics["ai4s_false"],
        metrics["passed_threshold"],
        metrics["errors"],
        metrics["cost_usd"],
    )
    return metrics
