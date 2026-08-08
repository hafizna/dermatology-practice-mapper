"""Structured logging setup — spec §6 (structured logging), §4.2 base scraper
requirement, and §14 source-drift reporting.

Every log line that touches scraping/parsing should be structured (key-value)
so that later we can grep/filter by hospital_slug, group_name, status, etc.
without parsing free-text messages.
"""

from __future__ import annotations

import logging
import sys

import structlog

from src.config import get_settings


def configure_logging(*, force: bool = False) -> None:
    """Configure structlog + stdlib logging. Idempotent unless force=True."""
    if structlog.is_configured() and not force:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.types.Processor
    if settings.log_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)
