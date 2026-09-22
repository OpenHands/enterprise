from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from fastapi import HTTPException

from .models import HttpRequestTemplate, ManagedConnectorTool
from .url_security import urlopen_no_redirect, validate_external_url


READ_ONLY_METHODS = {"GET", "HEAD"}


def default_access_mode_for_method(method: str) -> str:
    return "enabled" if method.upper() in READ_ONLY_METHODS else "approval_required"


OPENAPI_METHODS = ("get", "post", "put", "patch", "delete")


def normalize_tool_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return normalized or "call_api"


def resolve_openapi_ref(ref: str, document: dict[str, Any]) -> Any:
    if not ref.startswith("#/"):
        raise HTTPException(
            status_code=500, detail=f"Unsupported OpenAPI reference '{ref}'."
        )
    value: Any = document
    for segment in ref[2:].split("/"):
        segment = segment.replace("~1", "/").replace("~0", "~")
        if not isinstance(value, dict) or segment not in value:
            raise HTTPException(
                status_code=500, detail=f"Could not resolve OpenAPI reference '{ref}'."
            )
        value = value[segment]
    return value


def resolve_openapi_item(item: Any, document: dict[str, Any]) -> Any:
    if isinstance(item, dict) and isinstance(item.get("$ref"), str):
        return resolve_openapi_ref(item["$ref"], document)
    return item


def openapi_content_type(content: dict[str, Any]) -> str | None:
    if "application/json" in content:
        return "json"
    if "application/x-www-form-urlencoded" in content:
        return "form_urlencoded"
    if "multipart/form-data" in content:
        return "multipart_form_data"
    if "text/plain" in content:
        return "text"
    return None


def openapi_body_description(request_body: dict[str, Any] | None) -> str:
    content = request_body.get("content") if isinstance(request_body, dict) else None
    if isinstance(content, dict):
        if "multipart/form-data" in content:
            return "Provide multipart form fields under `body`. File inputs can use objects like { filename, contentType, contentBase64 }."
        if "application/x-www-form-urlencoded" in content:
            return "Provide form fields under `body`."
        if "text/plain" in content:
            return "Provide the plain-text request payload under `body`."
    return "Provide the request payload under `body`."


def build_openapi_description(
    operation: dict[str, Any],
    path_params: list[dict[str, Any]],
    query_params: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
) -> str:
    base = str(
        operation.get("summary") or operation.get("description") or "HTTP API operation"
    ).strip()
    hints: list[str] = []
    if path_params:
        hints.append(
            "Path params: " + ", ".join(param["name"] for param in path_params) + "."
        )
    if query_params:
        hints.append(
            "Query params: " + ", ".join(param["name"] for param in query_params) + "."
        )
    if request_body:
        hints.append(openapi_body_description(request_body))
    return f"{base} {' '.join(hints)}" if hints else base


def build_openapi_request_template(
    method: str,
    path: str,
    parameters: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
) -> dict[str, Any] | None:
    content = request_body.get("content") if isinstance(request_body, dict) else None
    content_type = openapi_content_type(content) if isinstance(content, dict) else None
    if content is not None and content_type is None:
        return None
    headers = {
        param["name"]: str(param.get("schema", {}).get("enum", [None])[0])
        for param in parameters
        if param.get("in") == "header"
        and str(param.get("name", "")).lower() != "authorization"
        and isinstance(param.get("schema"), dict)
        and len(param["schema"].get("enum", [])) == 1
    }
    query = {
        param["name"]: "{{" + param["name"] + "}}"
        for param in parameters
        if param.get("in") == "query"
    }
    return {
        "method": method.upper(),
        "path": re.sub(r"\{([^}]+)\}", r"{{\1}}", path),
        **({"query": query} if query else {}),
        **({"headers": headers} if headers else {}),
        **({"body": "{{body}}"} if request_body else {}),
        **({"contentType": content_type} if content_type else {}),
    }


def openapi_input_schema(
    parameters: list[dict[str, Any]], request_body: dict[str, Any] | None
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in parameters:
        name = param.get("name")
        if not isinstance(name, str) or param.get("in") not in {"path", "query"}:
            continue
        properties[name] = (
            param.get("schema")
            if isinstance(param.get("schema"), dict)
            else {"type": "string"}
        )
        if param.get("required") or param.get("in") == "path":
            required.append(name)
    if request_body:
        properties["body"] = {"description": "Request body."}
        if request_body.get("required"):
            required.append("body")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }


def generate_openapi_tools(
    open_api_url: str, auth_strategy: str
) -> list[ManagedConnectorTool]:
    validated_open_api_url = validate_external_url(
        open_api_url, purpose="OpenAPI schema"
    )
    try:
        request = urllib.request.Request(
            validated_open_api_url, headers={"Accept": "application/json"}
        )
        with urlopen_no_redirect(request, timeout=20) as response:
            document = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load OpenAPI schema from {open_api_url} ({exc.code}).",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load OpenAPI schema from {open_api_url}: {exc}",
        ) from exc
    paths = document.get("paths") if isinstance(document, dict) else None
    if not isinstance(paths, dict):
        raise HTTPException(
            status_code=500,
            detail=f"OpenAPI schema at {open_api_url} does not contain any paths.",
        )
    tools: list[ManagedConnectorTool] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        path_parameters = [
            resolve_openapi_item(item, document)
            for item in path_item.get("parameters", [])
            if isinstance(item, dict)
        ]
        for method in OPENAPI_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            security = operation.get("security", document.get("security", []))
            if (
                auth_strategy in {"oauth2", "bearer"}
                and security
                and not any(
                    "bearerAuth" in requirement
                    for requirement in security
                    if isinstance(requirement, dict)
                )
            ):
                continue
            parameters = [
                *path_parameters,
                *[
                    resolve_openapi_item(item, document)
                    for item in operation.get("parameters", [])
                    if isinstance(item, dict)
                ],
            ]
            request_body = (
                resolve_openapi_item(operation.get("requestBody"), document)
                if operation.get("requestBody")
                else None
            )
            request_template = build_openapi_request_template(
                method,
                str(path),
                parameters,
                request_body if isinstance(request_body, dict) else None,
            )
            if not request_template:
                continue
            tool_name = normalize_tool_name(
                str(operation.get("operationId") or f"{method}_{path}")
            )
            path_params = [param for param in parameters if param.get("in") == "path"]
            query_params = [param for param in parameters if param.get("in") == "query"]
            tools.append(
                ManagedConnectorTool(
                    name=tool_name,
                    accessMode=default_access_mode_for_method(method),
                    description=build_openapi_description(
                        operation,
                        path_params,
                        query_params,
                        request_body if isinstance(request_body, dict) else None,
                    ),
                    request=HttpRequestTemplate.model_validate(request_template),
                    config={
                        "inputSchema": openapi_input_schema(
                            parameters,
                            request_body if isinstance(request_body, dict) else None,
                        )
                    },
                )
            )
    if not tools:
        raise HTTPException(
            status_code=500,
            detail=f"OpenAPI schema at {open_api_url} did not produce any supported HTTP tools.",
        )
    return tools
