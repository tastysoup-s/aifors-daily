import json
import logging
import os
import re

from litellm import acompletion

from src.config import Models


logger = logging.getLogger(__name__)


class LLMError(Exception):
    def __init__(self, message: str, *, cost_usd: float = 0.0):
        super().__init__(message)
        self.cost_usd = cost_usd


# Map provider prefix -> env var litellm reads.
_PROVIDER_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
}


def _provider_of(model: str) -> str:
    if "/" not in model:
        raise LLMError(f"model {model!r} must be 'provider/name'")
    return model.split("/", 1)[0]


def check_api_keys(models: Models) -> None:
    """Reverse-lookup env vars for both scorer and summarizer; raise if any missing."""
    missing: list[str] = []
    for m in (models.scorer, models.summarizer):
        provider = _provider_of(m)
        env = _PROVIDER_ENV.get(provider)
        if env is None:
            raise LLMError(f"unknown provider {provider!r} (model {m}); add it to _PROVIDER_ENV")
        if not os.environ.get(env):
            missing.append(env)
    if missing:
        raise LLMError(
            "Missing API key env vars for configured models: "
            + ", ".join(sorted(set(missing)))
        )


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def parse_json_loose(text: str) -> dict:
    """Strip code fences, fix simple trailing commas, then json.loads."""
    cleaned = _FENCE_RE.sub("", text.strip())
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMError(f"could not parse JSON: {e}; raw={text[:200]!r}") from e


async def complete_json(
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float = 0.2,
    max_attempts: int = 2,
) -> tuple[dict, float]:
    """Call LLM and return parsed JSON plus total provider cost."""
    if max_attempts <= 0:
        raise ValueError("max_attempts must be greater than zero")
    total_cost = 0.0
    last_err: Exception | None = None
    last_raw: str = ""

    for attempt in range(max_attempts):
        messages = [{"role": "user", "content": prompt}]
        if attempt == 1:
            messages = [
                {"role": "user",
                 "content": prompt
                 + f"\n\n[Previous attempt returned invalid JSON: {last_raw[:120]!r}. "
                   f"Please output ONLY valid JSON.]"},
            ]
        try:
            response = await acompletion(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as e:  # litellm wraps provider errors
            raise LLMError(f"LLM call failed: {e}") from e

        content = response.choices[0].message.content or ""
        cost = float(getattr(response, "_hidden_params", {}).get("response_cost") or 0.0)
        total_cost += cost
        last_raw = content
        try:
            return parse_json_loose(content), total_cost
        except LLMError as e:
            last_err = e
            logger.warning("LLM %s attempt %d returned unparseable JSON: %s", model, attempt + 1, e)

    assert last_err is not None
    raise LLMError(
        f"LLM {model} produced unparseable JSON after {max_attempts} attempts: {last_err}",
        cost_usd=total_cost,
    )
