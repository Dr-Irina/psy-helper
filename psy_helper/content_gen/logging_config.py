"""Structured JSON logging для content engine.

Каждое событие = одна JSON-строка в stdout, поля плоские и стабильные.
Цели:
    - грепать по therapist_id / voice_profile / channel / status
    - агрегировать стоимость через jq из логов
    - не привязываться к Grafana/ELK в MVP

Использование:
    from psy_helper.content_gen.logging_config import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
    log.info("draft_generated", draft_id=str(d.id), cost_usd=0.04, channel="tg_post")

Уровень управляется через env CONTENT_GEN_LOG_LEVEL (default INFO).
"""
from __future__ import annotations

import logging
import os
import sys

import structlog


_INITIALIZED = False


def setup_logging() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    level_name = os.getenv("CONTENT_GEN_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _INITIALIZED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _INITIALIZED:
        setup_logging()
    return structlog.get_logger(name)
