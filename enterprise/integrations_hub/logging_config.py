from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from openhands.sdk.utils.redact import redact_text_secrets
from pythonjsonlogger.json import JsonFormatter


_URL_WITH_QUERY_RE = re.compile(r"(https?://[^\s?'\"]+)\?[^\s'\"]+")


def sanitize_log_text(value: str) -> str:
    return _URL_WITH_QUERY_RE.sub(r"\1?<redacted>", redact_text_secrets(value))


def sanitize_access_path(value: str) -> str:
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


class RedactingJsonFormatter(JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        redacted_record = copy.copy(record)
        redacted_record.msg = sanitize_log_text(str(record.msg))
        if isinstance(record.args, tuple):
            redacted_record.args = tuple(
                sanitize_log_text(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            redacted_record.args = {
                key: sanitize_log_text(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return super().format(redacted_record)

    def formatException(self, exc_info: Any) -> str:
        formatted = super().formatException(exc_info)
        return sanitize_log_text(
            "\n".join(formatted) if isinstance(formatted, list) else formatted
        )


class UvicornAccessJsonFormatter(RedactingJsonFormatter):
    def add_fields(
        self,
        log_data: dict[str, Any],
        record: logging.LogRecord,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_data, record, message_dict)
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return
        client_addr, method, full_path, http_version, status_code = args[:5]
        log_data["http.client_ip"] = client_addr
        log_data["http.method"] = method
        log_data["http.url"] = sanitize_access_path(str(full_path))
        log_data["http.version"] = http_version
        log_data["http.status_code"] = (
            int(status_code)
            if isinstance(status_code, str) and status_code.isdigit()
            else status_code
        )


class RedactAccessPathFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 5:
            mutable_args = list(args)
            mutable_args[2] = sanitize_access_path(str(mutable_args[2]))
            record.args = tuple(mutable_args)
        return True


def get_uvicorn_logging_config() -> dict[str, Any]:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    base_format = (
        "%(message)s %(levelname)s %(name)s %(module)s %(funcName)s "
        "%(lineno)d %(exc_info)s"
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": RedactingJsonFormatter,
                "fmt": base_format,
                "rename_fields": {"levelname": "severity"},
                "timestamp": "ts",
            },
            "access_json": {
                "()": UvicornAccessJsonFormatter,
                "fmt": base_format,
                "rename_fields": {"levelname": "severity"},
                "timestamp": "ts",
            },
        },
        "filters": {"redact_access_path": {"()": RedactAccessPathFilter}},
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "access_json",
                "filters": ["redact_access_path"],
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {
                "handlers": ["default"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["access"],
                "level": level,
                "propagate": False,
            },
        },
        "root": {"handlers": ["default"], "level": level},
    }
