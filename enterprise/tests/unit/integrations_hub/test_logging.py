from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Request

from integrations_hub.logging_config import (
    RedactAccessPathFilter,
    RedactingJsonFormatter,
    UvicornAccessJsonFormatter,
    sanitize_access_path,
    sanitize_log_text,
)
from integrations_hub.main import unhandled_exception_handler


def test_access_log_formatter_emits_http_fields_without_query_values() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:1234",
            "GET",
            "/api/oauth/slack/callback?code=secret&state=secret-state",
            "1.1",
            200,
        ),
        None,
    )
    assert RedactAccessPathFilter().filter(record)

    output = json.loads(UvicornAccessJsonFormatter().format(record))

    assert output["http.method"] == "GET"
    assert output["http.url"] == "/api/oauth/slack/callback"
    assert output["http.status_code"] == 200
    assert "secret" not in output["message"]


def test_log_sanitizers_remove_query_strings_and_api_keys() -> None:
    assert sanitize_access_path("/callback?code=secret") == "/callback"
    assert sanitize_log_text("https://example.com/callback?code=secret") == (
        "https://example.com/callback?<redacted>"
    )
    assert "ghp_" not in sanitize_log_text("ghp_abcdefghijklmnopqrstuvwxyz123456")
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "downstream failed: %s",
        ("https://example.com/callback?code=secret",),
        None,
    )
    assert "secret" not in RedactingJsonFormatter().format(record)


def test_unhandled_exception_response_has_correlation_ids(caplog) -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/failure",
            "raw_path": b"/api/failure",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
            "state": {"request_id": "request-123"},
        }
    )

    with caplog.at_level(logging.ERROR, logger="integrations_hub"):
        response = asyncio.run(
            unhandled_exception_handler(request, RuntimeError("failed"))
        )

    body = json.loads(bytes(response.body))
    assert response.status_code == 500
    assert response.headers["x-request-id"] == "request-123"
    assert response.headers["x-error-id"] == body["error_id"]
    assert caplog.records[0].event == "http.request.unhandled_exception"
    assert caplog.records[0].request_id == "request-123"
