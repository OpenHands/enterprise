from __future__ import annotations

import json
from typing import ClassVar

from integrations_hub import openapi_tools
from integrations_hub.openapi_tools import generate_openapi_tools


class FakeResponse:
    headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}
    status = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def test_generated_openapi_mutating_tools_require_approval(monkeypatch):
    document = {
        "openapi": "3.0.0",
        "paths": {
            "/items": {
                "get": {
                    "operationId": "listItems",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "operationId": "createItem",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/items/{id}": {
                "delete": {
                    "operationId": "deleteItem",
                    "parameters": [
                        {
                            "name": "id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"204": {"description": "Deleted"}},
                }
            },
        },
    }

    monkeypatch.setattr(
        openapi_tools,
        "urlopen_no_redirect",
        lambda *_args, **_kwargs: FakeResponse(document),
    )

    tools = {
        tool.name: tool
        for tool in generate_openapi_tools(
            "https://api.example.com/openapi.json", "none"
        )
    }

    assert tools["listitems"].accessMode == "enabled"
    assert tools["createitem"].accessMode == "approval_required"
    assert tools["deleteitem"].accessMode == "approval_required"
