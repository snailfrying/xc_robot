from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime


class SessionTraceWriter:
    def __init__(self, path: Path | None):
        self._path = path

    @property
    def path(self) -> Path | None:
        return self._path

    def write(self, event_type: str, payload: dict[str, object]) -> None:
        if self._path is None:
            return
        line = {
            "ts": datetime.now().astimezone().isoformat(),
            "event": event_type,
            "payload": payload,
        }
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False))
            handle.write("\n")


def get_component_logger(logger: logging.Logger, component_name: str) -> logging.Logger:
    return logger.getChild(component_name)


def configure_logging(settings):
    log_dir = settings.project_root / settings.logging.directory
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(settings.project_name)
    logger.setLevel(getattr(logging, settings.logging.level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if settings.logging.console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if settings.logging.file:
        file_handler = RotatingFileHandler(
            log_dir / "xc_rebot_agent.log",
            maxBytes=settings.logging.max_bytes,
            backupCount=settings.logging.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    trace_path = None
    if settings.logging.session_trace_enabled:
        trace_path = log_dir / "session_trace.jsonl"

    return logger, SessionTraceWriter(trace_path)
