from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib

from yomi_corpus.paths import resolve_repo_path

DEFAULT_PRICING_CONFIG_PATH = "config/pricing/openai_models.toml"


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    cached_input_per_1m: float
    output_per_1m: float


@dataclass(frozen=True)
class PricingEstimate:
    model: str
    processing_tier: str
    input_tokens: int
    cached_input_tokens: int
    billable_input_tokens: int
    output_tokens: int
    estimated_input_cost_usd: float
    estimated_cached_input_cost_usd: float
    estimated_output_cost_usd: float
    estimated_total_cost_usd: float


def load_model_pricing(
    model: str,
    processing_tier: str,
    *,
    pricing_config_path: str | Path = DEFAULT_PRICING_CONFIG_PATH,
) -> ModelPricing:
    config_path = resolve_repo_path(str(pricing_config_path))
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)

    models = payload.get("models") or {}
    model_payload = models.get(model)
    if not isinstance(model_payload, dict):
        raise KeyError(f"No pricing config for model: {model}")

    tier_payload = model_payload.get(processing_tier)
    if not isinstance(tier_payload, dict):
        raise KeyError(f"No pricing config for model={model}, processing_tier={processing_tier}")

    return ModelPricing(
        input_per_1m=float(tier_payload["input_per_1m"]),
        cached_input_per_1m=float(tier_payload["cached_input_per_1m"]),
        output_per_1m=float(tier_payload["output_per_1m"]),
    )


def estimate_cost_usd(
    usage: dict[str, Any] | None,
    *,
    model: str,
    processing_tier: str,
    pricing_config_path: str | Path = DEFAULT_PRICING_CONFIG_PATH,
) -> PricingEstimate | None:
    if not usage:
        return None

    pricing = load_model_pricing(
        model,
        processing_tier,
        pricing_config_path=pricing_config_path,
    )
    input_tokens = int(usage.get("input_tokens") or 0)
    cached_input_tokens = int(usage.get("cached_input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    billable_input_tokens = max(input_tokens - cached_input_tokens, 0)

    input_cost = _tokens_to_cost_usd(billable_input_tokens, pricing.input_per_1m)
    cached_input_cost = _tokens_to_cost_usd(cached_input_tokens, pricing.cached_input_per_1m)
    output_cost = _tokens_to_cost_usd(output_tokens, pricing.output_per_1m)
    total_cost = input_cost + cached_input_cost + output_cost

    return PricingEstimate(
        model=model,
        processing_tier=processing_tier,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        billable_input_tokens=billable_input_tokens,
        output_tokens=output_tokens,
        estimated_input_cost_usd=input_cost,
        estimated_cached_input_cost_usd=cached_input_cost,
        estimated_output_cost_usd=output_cost,
        estimated_total_cost_usd=total_cost,
    )


def _tokens_to_cost_usd(tokens: int, rate_per_1m: float) -> float:
    return (tokens / 1_000_000.0) * rate_per_1m
