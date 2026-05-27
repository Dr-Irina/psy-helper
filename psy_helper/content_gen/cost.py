"""Расчёт стоимости одного API-вызова Anthropic с учётом cache-токенов.

Цены актуальны на момент написания (2026-05); меняй при обновлении прайса.
Источник: https://docs.anthropic.com/en/docs/about-claude/pricing

Cache write: 25% надбавка к input rate (1.25x).
Cache read:  10% от input rate (0.10x).
"""
from __future__ import annotations

from typing import Any


# USD per million tokens (input, output, cache_write, cache_read)
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,   # 1.25x input
        "cache_read": 0.30,    # 0.10x input
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,
        "cache_read": 0.10,
    },
    "claude-opus-4-7": {
        "input": 15.00,
        "output": 75.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
    },
}


def calculate_cost(usage: Any, model: str) -> dict[str, float | int]:
    """Принимает anthropic Usage object (или dict с теми же полями) → cost dict.

    Поля Usage:
        input_tokens                — не-кэшированный input
        output_tokens               — output
        cache_creation_input_tokens — записано в кэш (надбавка)
        cache_read_input_tokens     — прочитано из кэша (дешевле)
    """
    if model not in PRICING:
        raise ValueError(f"Unknown model for pricing: {model!r}. Known: {list(PRICING)}")

    rates = PRICING[model]

    # Поддержка и SDK object, и обычного dict
    get = (lambda k: getattr(usage, k, 0) or 0) if not isinstance(usage, dict) \
          else (lambda k: usage.get(k, 0) or 0)

    input_tokens          = get("input_tokens")
    output_tokens         = get("output_tokens")
    cache_creation_tokens = get("cache_creation_input_tokens")
    cache_read_tokens     = get("cache_read_input_tokens")

    cost_usd = (
        input_tokens          * rates["input"]       / 1_000_000
        + output_tokens         * rates["output"]      / 1_000_000
        + cache_creation_tokens * rates["cache_write"] / 1_000_000
        + cache_read_tokens     * rates["cache_read"]  / 1_000_000
    )

    return {
        "cost_usd": round(cost_usd, 6),
        "tokens_input": input_tokens,
        "tokens_output": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
    }
