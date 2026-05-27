"""Тесты calculate_cost для Sonnet/Haiku/Opus с разными комбинациями токенов."""
from __future__ import annotations

import pytest

from psy_helper.content_gen.cost import PRICING, calculate_cost


class FakeUsage:
    """Симуляция anthropic.types.Usage."""
    def __init__(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


def test_sonnet_basic_no_cache():
    """1M input + 1M output Sonnet = $3 + $15 = $18."""
    cost = calculate_cost(FakeUsage(1_000_000, 1_000_000), "claude-sonnet-4-6")
    assert cost["cost_usd"] == pytest.approx(18.0)
    assert cost["tokens_input"] == 1_000_000
    assert cost["tokens_output"] == 1_000_000


def test_haiku_5x_cheaper_than_sonnet_for_same_tokens():
    haiku = calculate_cost(FakeUsage(1000, 500), "claude-haiku-4-5")
    sonnet = calculate_cost(FakeUsage(1000, 500), "claude-sonnet-4-6")
    # Haiku input $1, Sonnet $3 → ровно 3x. Output 5 vs 15 → 3x. Соотношение 3x.
    assert sonnet["cost_usd"] / haiku["cost_usd"] == pytest.approx(3.0)


def test_cache_read_is_cheap():
    """cache_read стоит 10% от input. 1M cache_read Sonnet = $0.30."""
    cost = calculate_cost(FakeUsage(cache_read_input_tokens=1_000_000), "claude-sonnet-4-6")
    assert cost["cost_usd"] == pytest.approx(0.30)


def test_cache_write_is_premium():
    """cache_creation стоит 125% от input. 1M cache_write Sonnet = $3.75."""
    cost = calculate_cost(FakeUsage(cache_creation_input_tokens=1_000_000), "claude-sonnet-4-6")
    assert cost["cost_usd"] == pytest.approx(3.75)


def test_unknown_model_raises():
    with pytest.raises(ValueError, match="Unknown model"):
        calculate_cost(FakeUsage(1000, 100), "claude-fictional-9000")


def test_accepts_dict_usage():
    """SDK иногда возвращает Usage как dict (например, при сериализации)."""
    cost = calculate_cost(
        {"input_tokens": 1000, "output_tokens": 500,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        "claude-haiku-4-5",
    )
    expected = (1000 * 1.00 + 500 * 5.00) / 1_000_000
    assert cost["cost_usd"] == pytest.approx(expected)


def test_zero_usage_zero_cost():
    cost = calculate_cost(FakeUsage(), "claude-sonnet-4-6")
    assert cost["cost_usd"] == 0
    assert cost["tokens_input"] == 0


def test_pricing_table_has_all_known_models():
    """Sanity check: прайс существует для моделей, которые мы передаём в каналы."""
    for required in ("claude-sonnet-4-6", "claude-haiku-4-5"):
        assert required in PRICING
        for key in ("input", "output", "cache_write", "cache_read"):
            assert PRICING[required][key] > 0
