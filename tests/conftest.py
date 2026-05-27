"""Общие fixtures для тестов content_gen.

Изолируем тесты от LRU-кэша loaders — каждый тест получает чистый кэш.
"""
from __future__ import annotations

import pytest

from psy_helper.content_gen import loaders


@pytest.fixture(autouse=True)
def _clear_loader_cache():
    """Сбрасываем LRU-кэш до и после каждого теста — изоляция."""
    loaders.clear_cache()
    yield
    loaders.clear_cache()
