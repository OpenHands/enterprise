from __future__ import annotations

import base64
import json
import logging
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.routing import APIRoute
from openhands.sdk.utils.redact import sanitize_dict
from openhands_extensions import (
    IntegrationCatalogEntry,
    IntegrationConnectionOption,
    list_integration_catalog_models,
)
from pydantic import ValidationError

from .auth import (
    AuthUnavailable,
    OpenHandsUser,
    authenticate_cookie,
    is_admin_owner,
)
from .config import get_default_config
from .execution import BlockingExecutors
from .integration_catalog import connection_defaults_from_option
from .managed_connectors import (
    _is_default_managed_connector,
    default_managed_connector,
    get_managed_connector,
    list_managed_connectors,
)
from .models import (
    AccessRequest,
    AccessRequestApprovalRequest,
    AdminConnectionFilters,
    AgentInvocationRequest,
    DisableUnusedToolsRequest,
    HttpRequestTemplate,
    IntegrationDiscoveryRequest,
    IntegrationRequestCreate,
    IntegrationRequestRecord,
    IntegrationSpec,
    IntegrationToggleRequest,
    ManagedConnector,
    ManagedConnectorTool,
    NotificationRecord,
    OAuthConnection,
    PermissionProfile,
    PermissionProfileApplyResult,
    PermissionProfileCreateRequest,
    PermissionProfileIntegrationSnapshot,
    PermissionProfilePatchRequest,
    PermissionProfileSnapshot,
    ToolAccessMode,
    ToolAccessPatch,
    ToolSpec,
    now_iso,
)
from .oauth_flow import (
    build_oauth_callback_redirect,
    consume_oauth_redirect_target,
    credentials_for,
    ensure_managed_oauth_client,
    exchange_oauth_code,
    oauth_proxy_callback_target,
    start_oauth_redirect,
)
from .openapi_tools import generate_openapi_tools
from .public_paths import (
    internal_api_path,
    public_api_path,
    public_openapi_paths,
    public_ui_path,
)
from .repository import repository
from .route_manifest import ROUTES
from .static_files import mount_static_files
from .url_security import urlopen_no_redirect, validate_external_url


logger = logging.getLogger("integrations_hub")


_executor_init_lock = threading.Lock()


def request_executors(application: FastAPI) -> BlockingExecutors:
    """Return the process-local executors, creating a test/runtime fallback.

    Production Uvicorn processes initialize this service during FastAPI's
    lifespan. The lazy path keeps direct ASGI tests and hosts that do not run
    lifespan hooks safe.
    """
    executors = getattr(application.state, "request_executors", None)
    if isinstance(executors, BlockingExecutors):
        return executors
    with _executor_init_lock:
        executors = getattr(application.state, "request_executors", None)
        if isinstance(executors, BlockingExecutors):
            return executors
        config = get_default_config()
        executors = BlockingExecutors(
            max_concurrent_requests=config.max_concurrent_blocking_requests,
            readiness_timeout_seconds=config.readiness_timeout_seconds,
        )
        application.state.request_executors = executors
        return executors


@asynccontextmanager
async def app_lifespan(application: FastAPI):
    request_executors(application)
    try:
        yield
    finally:
        executors = getattr(application.state, "request_executors", None)
        if isinstance(executors, BlockingExecutors):
            executors.shutdown()
            del application.state.request_executors


app = FastAPI(
    title="OpenHands Integrations Hub Python Backend",
    version="0.1.0",
    description="FastAPI backend with route correspondence to the Next.js integrations hub API.",
    lifespan=app_lifespan,
)


async def run_blocking[T](request: Request, func: Callable[..., T], *args: Any) -> T:
    """Run existing synchronous request work without blocking the ASGI loop."""
    return await request_executors(request.app).run(func, *args)


def request_log_fields(
    request: Request,
    *,
    status_code: int | None = None,
    duration_ms: float | None = None,
    error_id: str | None = None,
) -> dict[str, Any]:
    route = request.scope.get("route")
    fields: dict[str, Any] = {
        "request_id": request.state.request_id,
        "http.method": request.method,
        "http.route": route.path if isinstance(route, APIRoute) else request.url.path,
        "http.url": getattr(request.state, "public_path", request.url.path),
    }
    if status_code is not None:
        fields["http.status_code"] = status_code
    if duration_ms is not None:
        fields["duration_ms"] = round(duration_ms, 3)
    if error_id is not None:
        fields["error_id"] = error_id
    return fields


def request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied):
        return supplied
    return uuid.uuid4().hex


@app.middleware("http")
async def log_request(request: Request, call_next):
    request.state.request_id = request_id(request)
    started_at = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path not in {"/api/health", "/api/live", "/api/ready"}:
        status_code = response.status_code
        logger.log(
            logging.ERROR if status_code >= 500 else logging.INFO,
            "request completed",
            extra={
                "event": "http.request.completed",
                **request_log_fields(
                    request,
                    status_code=status_code,
                    duration_ms=(time.perf_counter() - started_at) * 1000,
                ),
            },
        )
    return response


_UNSAFE_BROWSER_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MAX_SESSION_COOKIE_CHUNKS = 8


def extract_session_cookie(request: Request) -> str:
    """Read OpenHands' bounded, possibly chunked browser session cookie."""
    cookie_name = get_default_config().openhands_auth_cookie_name
    first = request.cookies.get(cookie_name)
    if not first:
        return ""
    parts = [first]
    for index in range(1, _MAX_SESSION_COOKIE_CHUNKS):
        part = request.cookies.get(f"{cookie_name}_{index}")
        if part is None:
            break
        parts.append(part)
    return "".join(parts)


def normalized_origin(value: str) -> str | None:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def cookie_request_origin_is_allowed(request: Request) -> bool:
    supplied = normalized_origin(request.headers.get("origin", ""))
    if not supplied:
        return False
    allowed: set[str] = set()
    configured = normalized_origin(get_default_config().openhands_base_url or "")
    if configured:
        allowed.add(configured)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    forwarded_host = request.headers.get(
        "x-forwarded-host", request.headers.get("host", "")
    )
    request_origin = normalized_origin(f"{forwarded_proto}://{forwarded_host}")
    if request_origin:
        allowed.add(request_origin)
    return supplied in allowed


@app.middleware("http")
async def apply_public_paths_and_cookie_csrf(request: Request, call_next):
    public_path = request.scope.get("path", "")
    request.state.public_path = public_path

    credential = extract_api_key(request)
    session_cookie = extract_session_cookie(request)
    if (
        request.method in _UNSAFE_BROWSER_METHODS
        and not credential
        and session_cookie
        and not cookie_request_origin_is_allowed(request)
    ):
        return JSONResponse(
            status_code=403,
            content={
                "detail": "Cookie-authenticated mutations require a same-origin request."
            },
        )

    rewritten_path = internal_api_path(public_path)
    if rewritten_path:
        request.scope["path"] = rewritten_path
        request.scope["raw_path"] = rewritten_path.encode()
    else:
        cfg = get_default_config()
        if (
            cfg.static_dir
            and cfg.root_path
            and not public_path.startswith("/api")
            and public_path != cfg.root_path
            and not public_path.startswith(f"{cfg.root_path}/")
        ):
            target = public_ui_path(public_path)
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(target)

    return await call_next(request)


@app.exception_handler(RequestValidationError)
@app.exception_handler(ValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError | ValidationError
):
    logger.info(
        "request validation failed",
        extra={
            "event": "http.request.validation_failed",
            "validation_error_count": len(exc.errors()),
            **request_log_fields(request, status_code=422),
        },
    )
    return JSONResponse(
        status_code=422,
        content={"detail": sanitize_dict(exc.errors())},
        headers={"X-Request-ID": request.state.request_id},
    )


@app.exception_handler(HTTPException)
async def logged_http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 500:
        error_id = uuid.uuid4().hex
        logger.error(
            "handled server error",
            extra={
                "event": "http.request.server_error",
                "error.type": type(exc).__name__,
                **request_log_fields(
                    request, status_code=exc.status_code, error_id=error_id
                ),
            },
        )
        response = await http_exception_handler(request, exc)
        response.headers["X-Error-ID"] = error_id
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    error_id = uuid.uuid4().hex
    logger.error(
        "unhandled exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={
            "event": "http.request.unhandled_exception",
            "error.type": type(exc).__name__,
            **request_log_fields(request, status_code=500, error_id=error_id),
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error_id": error_id},
        headers={
            "X-Error-ID": error_id,
            "X-Request-ID": request.state.request_id,
        },
    )


RAW_CONNECTOR_INSTALL_FORBIDDEN_DETAIL = "Only app administrators can install integrations from arbitrary connector URLs. Use a managed connector instead."
RAW_CONNECTOR_DISCOVERY_FORBIDDEN_DETAIL = "Only app administrators can discover tools from arbitrary MCP server URLs. Ask an administrator to register a managed connector instead."
ADMIN_CONNECTOR_MANAGEMENT_DETAIL = (
    "Only app administrators can manage service-level connectors."
)
ADMIN_NOTIFICATIONS_DETAIL = "Only app administrators can view notifications."
ADMIN_KEY_MANAGEMENT_DETAIL = "Only app administrators can manage admin API keys."
ADMIN_API_SESSION_DETAIL = "Only app administrators can use admin API routes."


CASE_BY_CASE_APPROVAL_PATH = "/case-by-case-approvals"


def admin_connection_filters(request: Request) -> AdminConnectionFilters:
    return AdminConnectionFilters.model_validate(request.query_params)


def base_url(request: Request) -> str:
    return f"{request.url.scheme}://{request.url.netloc}"


def case_by_case_approval_url(request: Request) -> str:
    return f"{base_url(request)}{public_ui_path(CASE_BY_CASE_APPROVAL_PATH)}"


def human_approval_required_detail(
    approval_url: str, access_request_id: str | None = None
) -> str:
    parts = [
        "Human approval is required before calling this tool.",
        f"Ask a human to approve it at {approval_url}.",
    ]
    if access_request_id:
        parts.append(f"Request ID: {access_request_id}.")
    return " ".join(parts)


def agent_openapi(
    owner: str, request: Request, profile: PermissionProfile | None = None
) -> dict[str, Any]:
    connections = repository.list_connections(owner)
    connection_keys = {item["integrationKey"] for item in connections}
    connection_ids = {item["connectionId"] for item in connections}
    paths: dict[str, Any] = {}
    for integration in profiled_integrations(owner, profile):
        if not integration.enabled or (
            integration.authStrategy == "oauth2"
            and integration.key not in connection_keys
            and integration.config.get("connectionId") not in connection_ids
        ):
            continue
        for tool in integration.tools.values():
            if not tool.enabled or tool.accessMode == "disabled":
                continue
            paths[f"/api/agent/{integration.key}/{tool.name}"] = {
                "post": {
                    "summary": tool.description or tool.name,
                    "description": "Invocation identity is optional; agentId and agentClass default to null when omitted. Enabled tools are callable by default unless you mark them for case-by-case approval.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "examples": {
                                    "default": {
                                        "value": {
                                            "scopes": tool.defaultScopes,
                                            "payload": {},
                                        }
                                    }
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Tool invocation result"}},
                    "security": [{"AgentApiKey": []}],
                }
            }
    return {
        "openapi": "3.0.3",
        "info": {"title": "OpenHands Integrations Hub Agent API", "version": "0.1.0"},
        "servers": [{"url": base_url(request)}],
        "paths": public_openapi_paths(paths),
        "components": {
            "securitySchemes": {"AgentApiKey": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "AgentInvokeRequest": {"type": "object"},
                "AgentInvocationResult": {"type": "object"},
                "ErrorResponse": {"type": "object"},
            },
        },
    }


def user_openapi(request: Request, is_admin: bool) -> dict[str, Any]:
    json_content = {"application/json": {"schema": {"type": "object"}}}
    paths = {
        "/api/user/integrations": {
            "get": {
                "summary": "List owner integrations",
                "responses": {
                    "200": {
                        "description": "Owner integrations",
                        "content": json_content,
                    }
                },
            },
            "post": {
                "summary": "Install an owner integration",
                "requestBody": {"required": True, "content": json_content},
                "responses": {
                    "200": {"description": "Saved integration", "content": json_content}
                },
            },
        },
        "/api/user/integrations/{integrationKey}": {
            "delete": {
                "summary": "Delete an owner integration",
                "parameters": [
                    {
                        "name": "integrationKey",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {"description": "Delete result", "content": json_content}
                },
            }
        },
        "/api/user/integrations/{integrationKey}/tools/{toolName}": {
            "patch": {
                "summary": "Set owner tool access mode",
                "parameters": [
                    {
                        "name": "integrationKey",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "toolName",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "accessMode": {
                                        "type": "string",
                                        "enum": [
                                            "enabled",
                                            "disabled",
                                            "approval_required",
                                        ],
                                    }
                                },
                                "required": ["accessMode"],
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "Updated tool", "content": json_content}
                },
            }
        },
        "/api/user/connections": {
            "get": {
                "summary": "List owner OAuth/API connections",
                "responses": {
                    "200": {"description": "Connections", "content": json_content}
                },
            }
        },
        "/api/user/access-requests": {
            "get": {
                "summary": "List owner access requests",
                "responses": {
                    "200": {"description": "Access requests", "content": json_content}
                },
            }
        },
        "/api/user/access-requests/{requestId}/approve": {
            "post": {
                "summary": "Approve an access request",
                "parameters": [
                    {
                        "name": "requestId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {"required": False, "content": json_content},
                "responses": {
                    "200": {"description": "Temporary grant", "content": json_content}
                },
            }
        },
        "/api/user/access-requests/{requestId}/reject": {
            "post": {
                "summary": "Reject an access request",
                "parameters": [
                    {
                        "name": "requestId",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {"description": "Rejected request", "content": json_content}
                },
            }
        },
        "/api/user/key": {
            "get": {
                "summary": "Reveal user automation API key status/value",
                "responses": {
                    "200": {"description": "User key", "content": json_content}
                },
            },
            "post": {
                "summary": "Rotate user automation API key",
                "responses": {
                    "200": {"description": "New user key", "content": json_content}
                },
            },
            "delete": {
                "summary": "Revoke user automation API key",
                "responses": {
                    "200": {"description": "Revocation result", "content": json_content}
                },
            },
        },
        "/api/user/agent-key": {
            "get": {
                "summary": "Get or create restricted agent API key",
                "responses": {
                    "200": {"description": "Agent key", "content": json_content}
                },
            },
            "post": {
                "summary": "Rotate restricted agent API key",
                "responses": {
                    "200": {"description": "New agent key", "content": json_content}
                },
            },
        },
    }
    if is_admin:
        paths["/api/admin/modify-integrations"] = {
            "get": {
                "summary": "List managed connectors",
                "responses": {
                    "200": {
                        "description": "Managed connectors",
                        "content": json_content,
                    }
                },
            },
            "post": {
                "summary": "Create or update a managed connector",
                "requestBody": {"required": True, "content": json_content},
                "responses": {
                    "200": {
                        "description": "Saved managed connector",
                        "content": json_content,
                    }
                },
            },
        }
    return {
        "openapi": "3.0.3",
        "info": {"title": "OpenHands Integrations Hub User API", "version": "0.1.0"},
        "servers": [{"url": base_url(request)}],
        "security": [{"UserApiKey": []}],
        "tags": [
            {"name": "Integrations"},
            {"name": "Case-by-case approvals"},
            {"name": "API keys"},
        ],
        "paths": public_openapi_paths(paths),
        "components": {
            "securitySchemes": {"UserApiKey": {"type": "http", "scheme": "bearer"}},
            "schemas": {
                "IntegrationSpec": {"type": "object"},
                "ToolSpec": {"type": "object"},
                "AccessRequest": {"type": "object"},
                "TemporaryGrant": {"type": "object"},
                "ErrorResponse": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                },
            },
        },
    }


def managed_openapi(request: Request) -> dict[str, Any]:
    json_content = {"application/json": {"schema": {"type": "object"}}}
    document = {
        "openapi": "3.0.3",
        "info": {
            "title": "Modify Integrations API",
            "version": "1.0.0",
            "description": "Create, list, index, and delete managed connector definitions used by the integrations hub.",
        },
        "servers": [{"url": base_url(request)}],
        "security": [{"UserOrAdminSession": []}],
        "tags": [{"name": "Modify Integrations"}],
        "paths": {
            "/api/admin/modify-integrations": {
                "get": {
                    "tags": ["Modify Integrations"],
                    "summary": "List managed connectors",
                    "responses": {
                        "200": {
                            "description": "Managed connectors",
                            "content": json_content,
                        }
                    },
                },
                "post": {
                    "tags": ["Modify Integrations"],
                    "summary": "Create or update a managed connector",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "$ref": "#/components/schemas/ManagedConnectorUpsert"
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Saved connector",
                            "content": json_content,
                        },
                        "403": {"description": "Admin required"},
                    },
                },
            },
            "/api/admin/modify-integrations/{slug}": {
                "delete": {
                    "tags": ["Modify Integrations"],
                    "summary": "Delete a managed connector",
                    "parameters": [
                        {
                            "name": "slug",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Delete result",
                            "content": json_content,
                        },
                        "403": {"description": "Admin required"},
                    },
                }
            },
            "/api/admin/modify-integrations/{slug}/functions": {
                "post": {
                    "tags": ["Modify Integrations"],
                    "summary": "Index connector tool definitions",
                    "parameters": [
                        {
                            "name": "slug",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Updated connector",
                            "content": json_content,
                        },
                        "403": {"description": "Admin required"},
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "UserOrAdminSession": {"type": "http", "scheme": "bearer"}
            },
            "schemas": {
                "ManagedConnectorUpsert": {"type": "object"},
                "ManagedConnectorTool": {"type": "object"},
                "ManagedConnector": {"type": "object"},
                "ErrorResponse": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                },
            },
        },
    }
    document["paths"] = public_openapi_paths(document["paths"])
    return document


def admin_openapi(request: Request) -> dict[str, Any]:
    doc = user_openapi(request, True)
    doc["info"]["title"] = "OpenHands Integrations Hub Admin API"
    doc["security"] = [{"AdminApiKey": []}]
    doc["components"]["securitySchemes"] = {
        "AdminApiKey": {"type": "http", "scheme": "bearer"}
    }
    doc["paths"].update(
        {
            "/api/admin/connections": {
                "get": {
                    "summary": "List integration connections across owners",
                    "responses": {"200": {"description": "Admin connection summaries"}},
                }
            },
            "/api/admin/overview": {
                "get": {
                    "summary": "Get operational overview metrics",
                    "responses": {"200": {"description": "Operational overview"}},
                }
            },
            "/api/admin/access-requests": {
                "get": {
                    "summary": "List access requests across owners",
                    "responses": {"200": {"description": "Access requests"}},
                }
            },
            "/api/admin/integration-requests": {
                "get": {
                    "summary": "List user integration requests",
                    "responses": {"200": {"description": "Integration requests"}},
                }
            },
            "/api/admin/integration-requests/{requestId}/add": {
                "post": {
                    "summary": "Mark a user integration request as added",
                    "responses": {
                        "200": {"description": "Updated integration request"}
                    },
                }
            },
            "/api/admin/integration-requests/{requestId}/dismiss": {
                "post": {
                    "summary": "Dismiss a user integration request",
                    "responses": {
                        "200": {"description": "Updated integration request"}
                    },
                }
            },
            "/api/admin/key": {
                "get": {
                    "summary": "Reveal admin automation API key status/value",
                    "responses": {"200": {"description": "Admin key"}},
                },
                "post": {
                    "summary": "Rotate admin automation API key",
                    "responses": {"200": {"description": "New admin key"}},
                },
                "delete": {
                    "summary": "Revoke admin automation API key",
                    "responses": {"200": {"description": "Revocation result"}},
                },
            },
        }
    )
    doc["paths"] = public_openapi_paths(doc["paths"])
    return doc


def get_by_path(source: dict[str, Any], path: str) -> Any:
    value: Any = source
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def resolve_template(template: Any, payload: dict[str, Any]) -> Any:
    if (
        isinstance(template, str)
        and template.startswith("{{")
        and template.endswith("}}")
    ):
        return get_by_path(payload, template[2:-2].strip())
    if isinstance(template, list):
        return [
            value
            for item in template
            if (value := resolve_template(item, payload)) is not None
        ]
    if isinstance(template, dict):
        return {
            key: value
            for key, item in template.items()
            if (value := resolve_template(item, payload)) is not None
        }
    return template


def path_template(path: str, payload: dict[str, Any]) -> str:
    result = path
    for part in path.split("{{")[1:]:
        name = part.split("}}", 1)[0].strip()
        value = get_by_path(payload, name)
        if value is None or isinstance(value, (dict, list)):
            raise HTTPException(
                status_code=400,
                detail=f"Path placeholder '{name}' must resolve to a string, number, or boolean.",
            )
        result = result.replace(
            "{{" + name + "}}", urllib.parse.quote(str(value), safe="")
        )
    return result


def http_auth_headers(
    integration: IntegrationSpec, credentials: dict[str, Any]
) -> dict[str, str]:
    if integration.authStrategy == "api_key" and credentials.get("secret"):
        return {
            str(credentials.get("headerName") or "X-API-Key"): str(
                credentials["secret"]
            )
        }
    if integration.authStrategy in {"oauth2", "bearer"} and credentials.get(
        "accessToken"
    ):
        return {"Authorization": f"Bearer {credentials['accessToken']}"}
    if (
        integration.authStrategy == "basic"
        and credentials.get("username")
        and credentials.get("password")
    ):
        return {
            "Authorization": "Basic "
            + base64.b64encode(
                f"{credentials['username']}:{credentials['password']}".encode()
            ).decode()
        }
    return {}


def http_api_base_url(integration: IntegrationSpec) -> str:
    base = (
        integration.config.get("apiBaseUrl")
        if isinstance(integration.config, dict)
        else None
    )
    if not base and isinstance(integration.config, dict):
        slug = integration.config.get("managedConnectorSlug")
        connector = get_managed_connector(str(slug)) if slug else None
        base = connector.apiBaseUrl if connector else None
    return str(base or "").rstrip("/")


def multipart_value_bytes(value: Any) -> tuple[bytes, str | None, str | None]:
    if isinstance(value, dict):
        filename = str(value.get("filename")) if value.get("filename") else None
        content_type = (
            str(value.get("contentType")) if value.get("contentType") else None
        )
        if value.get("contentBase64"):
            return (
                base64.b64decode(str(value["contentBase64"])),
                filename,
                content_type or "application/octet-stream",
            )
        if value.get("text") is not None:
            return (
                str(value["text"]).encode(),
                filename,
                content_type or ("text/plain" if filename else None),
            )
        return json.dumps(value).encode(), filename, content_type or "application/json"
    return str(value).encode(), None, None


def encode_multipart_form(body: Any) -> tuple[bytes, str]:
    boundary = f"----openhands-{secrets.token_hex(16)}"
    body_record = body if isinstance(body, dict) else {}
    chunks: list[bytes] = []
    for key, value in body_record.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None:
                continue
            data, filename, content_type = multipart_value_bytes(item)
            disposition = f'form-data; name="{key}"'
            if filename:
                disposition += f'; filename="{filename}"'
            chunks.append(
                f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode()
            )
            if content_type:
                chunks.append(f"Content-Type: {content_type}\r\n".encode())
            chunks.append(b"\r\n")
            chunks.append(data)
            chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def invoke_http_tool(
    owner: str, integration: IntegrationSpec, tool: ToolSpec, payload: dict[str, Any]
) -> Any:
    base = http_api_base_url(integration)
    request_template_data = (
        tool.config.get("request") if isinstance(tool.config, dict) else None
    )
    if not base:
        raise HTTPException(
            status_code=400,
            detail=f"Integration '{integration.key}' is missing an apiBaseUrl configuration.",
        )
    if not isinstance(request_template_data, dict):
        raise HTTPException(
            status_code=400,
            detail=f"Tool '{tool.name}' is missing an HTTP request template.",
        )
    try:
        request_template = HttpRequestTemplate.model_validate(request_template_data)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Tool '{tool.name}' has an invalid HTTP request template.",
        ) from exc
    method = request_template.method.upper()
    validate_external_url(base, purpose="HTTP API base")
    path = path_template(request_template.path, payload).lstrip("/")
    url = f"{base}/{path}"
    query = resolve_template(request_template.query, payload)
    if isinstance(query, dict) and query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    headers = http_auth_headers(integration, credentials_for(owner, integration))
    header_values = resolve_template(request_template.headers, payload)
    if isinstance(header_values, dict):
        headers.update(
            {str(k): str(v) for k, v in header_values.items() if v is not None}
        )
    body = resolve_template(request_template.body, payload)
    data = None
    if method not in {"GET", "HEAD"} and body is not None:
        content_type = request_template.contentType
        if content_type == "form_urlencoded":
            data = urllib.parse.urlencode(
                body if isinstance(body, dict) else {}
            ).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif content_type == "multipart_form_data":
            data, multipart_content_type = encode_multipart_form(body)
            headers["Content-Type"] = multipart_content_type
        elif content_type == "text":
            data = (body if isinstance(body, str) else json.dumps(body)).encode()
            headers["Content-Type"] = "text/plain"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    validated_url = validate_external_url(url, purpose="HTTP tool request")
    req = urllib.request.Request(
        validated_url, data=data, headers=headers, method=method
    )
    try:
        with urlopen_no_redirect(req, timeout=20) as response:
            raw = response.read()
            content_type = response.headers.get("content-type", "")
            if response.status == 204:
                return None
            if "application/json" in content_type:
                return json.loads(raw.decode() or "null")
            if (
                content_type.startswith("text/")
                or "xml" in content_type
                or "html" in content_type
            ):
                return raw.decode()
            return {
                "contentType": content_type or "application/octet-stream",
                "sizeBytes": len(raw),
                "contentBase64": base64.b64encode(raw).decode(),
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise HTTPException(
            status_code=502,
            detail=f"{integration.name} {tool.name} failed ({exc.code}): {detail or exc.reason}",
        ) from exc


MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_CLIENT_INFO = {"name": "agent-integrations-hub-fastapi", "version": "0.2.0"}


def mcp_server_url(integration: IntegrationSpec, tool: ToolSpec) -> str:
    config = tool.config if isinstance(tool.config, dict) else {}
    server_url = config.get("serverUrl") or integration.config.get("serverUrl")
    if not server_url and isinstance(integration.config, dict):
        slug = integration.config.get("managedConnectorSlug")
        connector = get_managed_connector(str(slug)) if slug else None
        server_url = connector.serverUrl if connector else None
    if not isinstance(server_url, str) or not server_url.strip():
        raise HTTPException(
            status_code=400,
            detail=f"Integration '{integration.key}' is missing an MCP serverUrl configuration.",
        )
    return server_url.strip()


def mcp_tool_name(tool: ToolSpec) -> str:
    config = tool.config if isinstance(tool.config, dict) else {}
    tool_name = config.get("toolName")
    return (
        tool_name.strip()
        if isinstance(tool_name, str) and tool_name.strip()
        else tool.name
    )


def mcp_transport_urls(default_url: str, *configs: dict[str, Any]) -> list[str]:
    for config in configs:
        order = config.get("mcpTransportOrder") if isinstance(config, dict) else None
        if not isinstance(order, list):
            continue
        urls = [
            str(item.get("url")).strip()
            for item in order
            if isinstance(item, dict)
            and item.get("transport") == "streamable_http"
            and str(item.get("url") or "").strip()
        ]
        return urls or [default_url]
    return [default_url]


def retry_mcp_transports[T](
    urls: list[str], operation: Callable[[str], T], fallback: T
) -> T:
    last_error: HTTPException | None = None
    for url in urls:
        try:
            return operation(url)
        except HTTPException as exc:
            last_error = exc
        except httpx.HTTPError as exc:
            last_error = HTTPException(status_code=502, detail=str(exc))
    if last_error:
        raise last_error
    return fallback


def discover_mcp_tools_with_transport_order(
    server_url: str,
    headers: dict[str, str],
    *configs: dict[str, Any],
    timeout: float | None = None,
) -> dict[str, ToolSpec]:
    return retry_mcp_transports(
        mcp_transport_urls(server_url, *configs),
        lambda url: (
            discover_mcp_tools(url, headers, timeout=timeout)
            if timeout is not None
            else discover_mcp_tools(url, headers)
        ),
        {},
    )


def parse_sse_json_messages(text: str) -> list[Any]:
    messages: list[Any] = []
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line.strip():
            continue
        if data_lines:
            messages.append(json.loads("\n".join(data_lines)))
            data_lines = []
    if data_lines:
        messages.append(json.loads("\n".join(data_lines)))
    return messages


def parse_mcp_http_response(
    response: httpx.Response, expected_id: int | None
) -> tuple[dict[str, Any] | None, str | None]:
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.text or response.reason_phrase,
        )
    session_id = response.headers.get("mcp-session-id")
    if response.status_code == 202 or not response.content:
        return None, session_id
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        raw_messages = parse_sse_json_messages(response.text)
    else:
        payload = response.json()
        raw_messages = payload if isinstance(payload, list) else [payload]
    messages = [message for message in raw_messages if isinstance(message, dict)]
    message = next(
        (
            item
            for item in messages
            if expected_id is None or item.get("id") == expected_id
        ),
        messages[0] if messages else None,
    )
    if not message:
        return None, session_id
    if isinstance(message.get("error"), dict):
        error = message["error"]
        raise HTTPException(
            status_code=502, detail=str(error.get("message") or "MCP tool call failed.")
        )
    result = message.get("result")
    return (result if isinstance(result, dict) else {"result": result}), session_id


def post_mcp_message(
    client: httpx.Client,
    server_url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    session_id: str | None,
    protocol_version: str | None,
    expected_id: int | None,
) -> tuple[dict[str, Any] | None, str | None]:
    request_headers = {
        **headers,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        request_headers["mcp-session-id"] = session_id
    if protocol_version:
        request_headers["mcp-protocol-version"] = protocol_version
    response = client.post(server_url, headers=request_headers, json=body)
    return parse_mcp_http_response(response, expected_id)


def invoke_mcp_session(
    client: httpx.Client,
    server_url: str,
    headers: dict[str, str],
    tool_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    initialized, session_id = post_mcp_message(
        client,
        server_url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": MCP_CLIENT_INFO,
            },
        },
        None,
        None,
        1,
    )
    protocol_version = str(
        (initialized or {}).get("protocolVersion") or MCP_PROTOCOL_VERSION
    )
    post_mcp_message(
        client,
        server_url,
        headers,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id,
        protocol_version,
        None,
    )
    result, _ = post_mcp_message(
        client,
        server_url,
        headers,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": payload},
        },
        session_id,
        protocol_version,
        2,
    )
    return result or {}


def invoke_mcp_tool(
    owner: str, integration: IntegrationSpec, tool: ToolSpec, payload: dict[str, Any]
) -> Any:
    server_url = mcp_server_url(integration, tool)
    headers = http_auth_headers(integration, credentials_for(owner, integration))
    tool_name = mcp_tool_name(tool)
    config = tool.config if isinstance(tool.config, dict) else {}

    def call_transport(candidate_url: str) -> dict[str, Any]:
        validated_url = validate_external_url(candidate_url, purpose="MCP server")
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            return invoke_mcp_session(
                client, validated_url, headers, tool_name, payload
            )

    return retry_mcp_transports(
        mcp_transport_urls(server_url, config, integration.config), call_transport, {}
    )


def invoke_tool_payload(
    owner: str, integration: IntegrationSpec, tool: ToolSpec, payload: dict[str, Any]
) -> Any:
    if tool.executionMode == "mock":
        return (
            tool.mockResponse if tool.mockResponse is not None else {"payload": payload}
        )
    if integration.kind == "api":
        return invoke_http_tool(owner, integration, tool, payload)
    return invoke_mcp_tool(owner, integration, tool, payload)


def effective_scopes(tool: ToolSpec, requested_scopes: list[str]) -> list[str]:
    return sorted(set(requested_scopes or tool.defaultScopes))


def active_temporary_grant(
    owner: str,
    agent_id: str | None,
    agent_class: str | None,
    integration_key: str,
    tool_name: str,
    scopes: list[str],
) -> dict[str, Any] | None:
    current = now_iso()
    for grant in repository.list_temporary_grants(owner):
        if grant.get("agentId") != agent_id or grant.get("agentClass") != agent_class:
            continue
        if (
            grant.get("integrationKey") != integration_key
            or grant.get("toolName") != tool_name
        ):
            continue
        if str(grant.get("expiresAt") or "") <= current:
            continue
        grant_scopes = grant.get("scopes") or []
        if not grant_scopes or all(scope in grant_scopes for scope in scopes):
            return grant
    return None


def ensure_pending_access_request(
    owner: str,
    integration: IntegrationSpec,
    tool: ToolSpec,
    scopes: list[str],
    agent_id: str | None,
    agent_class: str | None,
) -> AccessRequest:
    existing = repository.find_matching_pending_access_request(
        owner, agent_id, agent_class, integration.key, tool.name, scopes
    )
    if existing:
        return existing
    return repository.save_access_request(
        owner,
        AccessRequest(
            agentId=agent_id,
            agentClass=agent_class,
            integrationKey=integration.key,
            toolName=tool.name,
            scopes=scopes,
        ),
    )


def deny_tool_invocation(
    owner: str,
    integration: IntegrationSpec,
    tool: ToolSpec,
    scopes: list[str],
    payload: dict[str, Any],
    agent_id: str | None,
    agent_class: str | None,
    reason: str,
    create_access_request: bool = False,
    approval_url: str = CASE_BY_CASE_APPROVAL_PATH,
) -> None:
    access_request_id = None
    if create_access_request:
        access_request = ensure_pending_access_request(
            owner, integration, tool, scopes, agent_id, agent_class
        )
        access_request_id = access_request.id
        reason = human_approval_required_detail(approval_url, access_request_id)
    repository.record_tool_invocation(
        owner,
        integration,
        tool,
        scopes,
        payload,
        "denied",
        reason,
        agent_id,
        agent_class,
    )
    raise HTTPException(status_code=403, detail=reason)


def invoke_enabled_tool(
    owner: str,
    integration: IntegrationSpec,
    tool: ToolSpec,
    scopes: list[str],
    payload: dict[str, Any],
    agent_id: str | None = None,
    agent_class: str | None = None,
    approval_url: str = CASE_BY_CASE_APPROVAL_PATH,
    persist_tool_metrics: bool = True,
) -> Any:
    scopes = effective_scopes(tool, scopes)
    grant = active_temporary_grant(
        owner, agent_id, agent_class, integration.key, tool.name, scopes
    )
    if tool.accessMode == "approval_required" and not grant:
        deny_tool_invocation(
            owner,
            integration,
            tool,
            scopes,
            payload,
            agent_id,
            agent_class,
            "Human approval is required before calling this tool.",
            True,
            approval_url,
        )

    tool.invocationCount = (tool.invocationCount or 0) + 1
    tool.lastInvokedAt = now_iso()
    try:
        data = invoke_tool_payload(owner, integration, tool, payload)
    except HTTPException as exc:
        tool.lastInvocationOutcome = "error"
        if persist_tool_metrics:
            repository.save_tool(owner, integration.key, tool)
        repository.record_tool_invocation(
            owner,
            integration,
            tool,
            scopes,
            payload,
            "error",
            str(exc.detail),
            agent_id,
            agent_class,
        )
        raise
    tool.lastInvocationOutcome = "success"
    if persist_tool_metrics:
        repository.save_tool(owner, integration.key, tool)
    repository.record_tool_invocation(
        owner,
        integration,
        tool,
        scopes,
        payload,
        "success",
        agent_id=agent_id,
        agent_class=agent_class,
    )
    return data


def mcp_content_text(data: Any) -> str:
    return data if isinstance(data, str) else json.dumps(data)


def root_template_variable(value: str) -> str:
    return value.split(".", 1)[0].strip()


def collect_template_variables(value: Any) -> set[str]:
    variables: set[str] = set()
    if isinstance(value, str):
        for match in re.finditer(r"{{\s*([A-Za-z0-9_.-]+)\s*}}", value):
            variable = root_template_variable(match.group(1))
            if variable:
                variables.add(variable)
    elif isinstance(value, list):
        for item in value:
            variables.update(collect_template_variables(item))
    elif isinstance(value, dict):
        for item in value.values():
            variables.update(collect_template_variables(item))
    return variables


def json_schema_properties(schema: Any) -> tuple[dict[str, Any], set[str]]:
    if (
        not isinstance(schema, dict)
        or schema.get("type") != "object"
        or not isinstance(schema.get("properties"), dict)
    ):
        return {}, set()
    required = (
        schema.get("required") if isinstance(schema.get("required"), list) else []
    )
    return dict(schema["properties"]), {str(item) for item in required}


def mcp_tool_input_schema(tool: ToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "agentId": {
            "type": ["string", "null"],
            "description": "Optional agent ID override for this call.",
        },
        "agentClass": {
            "type": ["string", "null"],
            "description": "Optional agent class override for this call.",
        },
        "scopes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional requested scopes for this call.",
        },
    }
    required: set[str] = set()
    config = tool.config if isinstance(tool.config, dict) else {}
    schema_properties, schema_required = json_schema_properties(
        config.get("inputSchema")
    )
    properties.update(schema_properties)
    required.update(schema_required)
    for variable in sorted(collect_template_variables(config.get("request"))):
        properties.setdefault(
            variable,
            {"description": f"Value for request template variable '{variable}'."},
        )
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(required),
        "additionalProperties": True,
    }


def mcp_tool_list(
    owner: str, profile: PermissionProfile | None = None
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for integration in profiled_integrations(owner, profile):
        if not integration.enabled:
            continue
        for tool in integration.tools.values():
            if not tool.enabled or tool.accessMode == "disabled":
                continue
            tools.append(
                {
                    "name": f"{integration.key}__{tool.name}",
                    "description": tool.description,
                    "inputSchema": mcp_tool_input_schema(tool),
                }
            )
    return tools


def jsonrpc_response(
    body: dict[str, Any], result: Any = None, error: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = {"jsonrpc": "2.0", "id": body.get("id")}
    if error is not None:
        response["error"] = error
    else:
        response["result"] = result
    return response


def mcp_call(
    owner: str,
    body: dict[str, Any],
    approval_url: str,
    profile: PermissionProfile | None = None,
) -> dict[str, Any] | Response:
    method = body.get("method")
    if method == "initialize":
        return jsonrpc_response(
            body,
            {
                "protocolVersion": body.get("params", {}).get(
                    "protocolVersion", "2024-11-05"
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "integrations-hub-fastapi", "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "tools/list":
        return jsonrpc_response(body, {"tools": mcp_tool_list(owner, profile)})
    if method == "tools/call":
        params = body.get("params", {})
        name = params.get("name", "")
        if "__" not in name:
            return jsonrpc_response(
                body,
                error={
                    "code": -32602,
                    "message": "Tool name must use integration__tool format.",
                },
            )
        integration_key, tool_name = name.split("__", 1)
        integration = profiled_integration(owner, integration_key, profile)
        if not integration:
            return jsonrpc_response(
                body, error={"code": -32004, "message": "Integration not found."}
            )
        tool = integration.tools.get(tool_name)
        if not tool or not tool.enabled or tool.accessMode == "disabled":
            return jsonrpc_response(
                body, error={"code": -32003, "message": "Tool is disabled."}
            )
        arguments = params.get("arguments", {})
        argument_payload = arguments if isinstance(arguments, dict) else {}
        requested_scopes = argument_payload.pop(
            "scopes",
            argument_payload.pop("contextScopes", params.get("scopes", [])),
        )
        scopes = requested_scopes if isinstance(requested_scopes, list) else []
        agent_id = argument_payload.pop(
            "agentId", argument_payload.pop("contextAgentId", None)
        )
        agent_class = argument_payload.pop(
            "agentClass", argument_payload.pop("contextAgentClass", None)
        )
        try:
            data = invoke_enabled_tool(
                owner,
                integration,
                tool,
                scopes,
                argument_payload,
                agent_id,
                agent_class,
                approval_url,
                profile is None,
            )
        except HTTPException as exc:
            return jsonrpc_response(
                body,
                error={"code": -32000 - exc.status_code, "message": str(exc.detail)},
            )
        return jsonrpc_response(
            body, {"content": [{"type": "text", "text": mcp_content_text(data)}]}
        )
    return jsonrpc_response(
        body, error={"code": -32601, "message": f"Unsupported MCP method: {method}"}
    )


def requires_dashboard_session(path: str) -> bool:
    if path in {
        "/api/user/key",
        "/api/user/agent-key",
        "/api/admin/key",
        "/api/oauth/connections",
        "/api/notifications",
        "/api/permission-profiles",
    }:
        return True
    if path.startswith("/api/permission-profiles/"):
        return True
    if path.startswith("/api/integrations"):
        return True
    if path == "/api/oauth/{provider}/start":
        return True
    return False


def requested_org_id_from_request(request: Request) -> str | None:
    value = request.headers.get("x-org-id")
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def openhands_user_from_request(request: Request) -> OpenHandsUser | None:
    requested_org_id = requested_org_id_from_request(request)
    cookie = extract_session_cookie(request)
    if cookie:
        return authenticate_cookie(cookie, requested_org_id)
    return None


def authorize_dashboard_user(request: Request) -> OpenHandsUser:
    try:
        user = openhands_user_from_request(request)
    except AuthUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="OpenHands Cloud is temporarily unavailable. Please retry.",
        ) from exc
    if user:
        return user
    raise HTTPException(
        status_code=401,
        detail="Sign in with OpenHands Cloud before using this endpoint.",
    )


def owner_is_admin(owner: str, user: OpenHandsUser | None = None) -> bool:
    if user and owner == user.owner_id:
        return user.is_admin
    return is_admin_owner(owner)


def extract_api_key(request: Request) -> str:
    authorization = (request.headers.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


def authorized_agent_record(request: Request, purpose: str) -> dict[str, Any]:
    if not repository.has_agent_api_keys():
        raise HTTPException(
            status_code=503,
            detail=f"Open the dashboard once to provision a personal integrations-hub API key before agents {purpose}.",
        )
    record = repository.find_agent_api_key(extract_api_key(request))
    if not record:
        raise HTTPException(
            status_code=401,
            detail="Provide a valid personal integrations-hub API key in the Authorization bearer header or X-API-Key.",
            headers={"WWW-Authenticate": 'Bearer realm="agent-integrations-hub"'},
        )
    record["ownerId"] = str(record["ownerId"]).lower()
    return record


def authorize_user_api_owner(request: Request) -> tuple[str, bool]:
    record = repository.find_scoped_api_key("user", extract_api_key(request))
    if not record:
        raise HTTPException(
            status_code=401,
            detail="Provide a valid user API key in the Authorization bearer header or X-API-Key.",
            headers={"WWW-Authenticate": 'Bearer realm="integrations-hub-user-api"'},
        )
    owner = str(record["ownerId"]).lower()
    return owner, is_admin_owner(owner)


def authorize_user_api_owner_or_session(request: Request) -> str:
    """Resolve the owner for a user-facing endpoint (e.g. case-by-case
    approvals) from one of:

    * a personal integrations-hub **user API key** (bearer / ``X-API-Key``),
      validated against the stored user keys; or
    * an OpenHands Cloud cookie-backed dashboard session, when the supplied
      credential is not a stored user key.

    When no stored user key is supplied, a valid OpenHands Cloud session is
    required. Raises HTTP 401 when a credential matches neither a user key nor
    a valid Cloud session, or when no credential/session is present.
    """
    credential = extract_api_key(request)
    if credential:
        record = repository.find_scoped_api_key("user", credential)
        if record:
            owner = str(record["ownerId"]).lower()
            return owner
        user = authorize_dashboard_user(request)
        return user.owner_id
    user = authorize_dashboard_user(request)
    return user.owner_id


def authorize_admin_api_owner(request: Request) -> str:
    api_key = extract_api_key(request)
    record = repository.find_scoped_api_key("admin", api_key) if api_key else None
    if record:
        owner = str(record["ownerId"]).lower()
        if ":" not in owner and not is_admin_owner(owner):
            raise HTTPException(status_code=403, detail=ADMIN_API_SESSION_DETAIL)
        return owner

    if api_key and (
        repository.find_agent_api_key(api_key)
        or repository.find_scoped_api_key("user", api_key)
    ):
        raise HTTPException(status_code=403, detail=ADMIN_API_SESSION_DETAIL)

    try:
        user = openhands_user_from_request(request)
    except AuthUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="OpenHands Cloud is temporarily unavailable. Please retry.",
        ) from exc
    if user:
        if not user.is_admin:
            raise HTTPException(status_code=403, detail=ADMIN_API_SESSION_DETAIL)
        return user.owner_id

    raise HTTPException(
        status_code=401,
        detail="Provide a valid admin API key or sign in with OpenHands Cloud as an app administrator.",
        headers={"WWW-Authenticate": 'Bearer realm="integrations-hub-admin-api"'},
    )


def optional_trimmed_config(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def require_app_admin(owner: str, detail: str, is_admin: bool | None = None) -> None:
    if not (owner_is_admin(owner) if is_admin is None else is_admin):
        raise HTTPException(status_code=403, detail=detail)


def assert_user_can_save_integration(
    owner: str, integration: IntegrationSpec, is_admin: bool | None = None
) -> None:
    if owner_is_admin(owner) if is_admin is None else is_admin:
        return
    managed_slug = optional_trimmed_config(integration.config, "managedConnectorSlug")
    api_base_url = optional_trimmed_config(integration.config, "apiBaseUrl")
    server_url = optional_trimmed_config(integration.config, "serverUrl")
    if not managed_slug or api_base_url or server_url:
        raise HTTPException(
            status_code=403, detail=RAW_CONNECTOR_INSTALL_FORBIDDEN_DETAIL
        )
    if not repository.get_managed_connector(managed_slug):
        raise HTTPException(
            status_code=404,
            detail=(
                f"Managed connector '{managed_slug}' is not available. An admin must "
                "add it before users can connect it."
            ),
        )


def assert_user_can_discover_integration(
    owner: str,
    payload: IntegrationDiscoveryRequest,
    is_admin: bool | None = None,
) -> None:
    if (owner_is_admin(owner) if is_admin is None else is_admin) or payload.is_managed:
        return
    raise HTTPException(
        status_code=403, detail=RAW_CONNECTOR_DISCOVERY_FORBIDDEN_DETAIL
    )


ACCESS_PRIORITY = {"disabled": 0, "approval_required": 1, "enabled": 2}


def subtract_unused_window(value: int, unit: str) -> str:
    reference = datetime.now(UTC)
    if unit == "weeks":
        cutoff = reference - timedelta(days=value * 7)
    elif unit == "months":
        month = reference.month - value
        year = reference.year
        while month <= 0:
            month += 12
            year -= 1
        day = min(reference.day, 28)
        cutoff = reference.replace(year=year, month=month, day=day)
    else:
        cutoff = reference - timedelta(days=value)
    return cutoff.isoformat().replace("+00:00", "Z")


def disable_unused_tools(
    owner: str, request: DisableUnusedToolsRequest
) -> dict[str, Any]:
    value = request.thresholdValue
    unit = request.thresholdUnit
    cutoff = subtract_unused_window(value, unit)
    usage = {
        (summary["integrationKey"], summary["toolName"]): summary
        for summary in repository.list_owner_tool_usage_summaries(owner)
        if str(summary.get("lastInvokedAt") or "") < cutoff
    }
    disabled: list[dict[str, Any]] = []
    for listed in repository.list_integrations(owner):
        integration = repository.get_integration(owner, listed.key)
        if not integration:
            continue
        changed = False
        for tool_name, tool in list(integration.tools.items()):
            if tool.accessMode == "disabled":
                continue
            summary = usage.get((integration.key, tool_name))
            if not summary:
                continue
            integration.tools[tool_name] = tool.model_copy(
                update={"accessMode": "disabled", "enabled": False}
            )
            disabled.append(
                {
                    "integrationKey": integration.key,
                    "toolName": tool_name,
                    "lastInvokedAt": summary["lastInvokedAt"],
                }
            )
            changed = True
        if changed:
            repository.save_integration(owner, integration)
    disabled.sort(key=lambda item: (item["integrationKey"], item["toolName"]))
    return {
        "thresholdValue": value,
        "thresholdUnit": unit,
        "cutoffIso": cutoff,
        "disabledCount": len(disabled),
        "disabledTools": disabled,
        "updatedIntegrations": repository.list_integrations(owner),
    }


def trimmed_profile_name(request: PermissionProfileCreateRequest) -> str:
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required.")
    if len(name) > 80:
        raise HTTPException(
            status_code=400, detail="Profile name must be 80 characters or fewer."
        )
    return name


def tool_access_mode(tool: ToolSpec) -> ToolAccessMode:
    if tool.accessMode:
        return tool.accessMode
    return "enabled" if tool.enabled else "disabled"


def current_permission_snapshot(owner: str) -> PermissionProfileSnapshot:
    return PermissionProfileSnapshot(
        integrations={
            integration.key: PermissionProfileIntegrationSnapshot(
                enabled=integration.enabled,
                tools={
                    tool_name: tool_access_mode(tool)
                    for tool_name, tool in integration.tools.items()
                },
            )
            for integration in repository.list_integrations(owner)
        }
    )


def ensure_default_permission_profile(
    owner: str, refresh: bool = False
) -> PermissionProfile:
    snapshot = current_permission_snapshot(owner)
    profile = repository.get_default_permission_profile(owner)
    if profile is None:
        return repository.save_permission_profile(
            owner,
            PermissionProfile(ownerId=owner, name=None, snapshot=snapshot),
        )
    if refresh or not profile.agentApiKey:
        profile = profile.model_copy(
            update={"snapshot": snapshot, "updatedAt": now_iso()}
        )
        return repository.save_permission_profile(owner, profile)
    return profile


def sync_default_permission_profile(owner: str) -> None:
    if repository.get_default_permission_profile(owner) is not None:
        ensure_default_permission_profile(owner, refresh=True)


def rotate_permission_profile_agent_key(
    owner: str, profile_id: str
) -> PermissionProfile:
    profile = repository.get_permission_profile(owner, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Permission profile not found.")

    key = repository.rotate_permission_profile_api_key(owner, profile.id)
    if profile.name is None:
        repository.agent_keys[owner] = str(key["api_key"])
    return repository.get_permission_profile(owner, profile.id) or profile.model_copy(
        update={"agentApiKey": str(key["api_key"]), "updatedAt": now_iso()}
    )


def agent_permission_profile(record: dict[str, Any]) -> PermissionProfile | None:
    owner = str(record["ownerId"]).lower()
    profile_id = record.get("permissionProfileId")
    if profile_id is None or profile_id == "":
        return ensure_default_permission_profile(owner, refresh=True)
    profile = repository.get_permission_profile(owner, str(profile_id))
    if profile and profile.name is None:
        return ensure_default_permission_profile(owner, refresh=True)
    return profile


def apply_permission_profile_to_integration(
    integration: IntegrationSpec, profile: PermissionProfile | None
) -> IntegrationSpec:
    if profile is None:
        return integration
    saved = profile.snapshot.integrations.get(integration.key)
    if saved is None:
        return integration.model_copy(update={"enabled": False})
    tools: dict[str, ToolSpec] = {}
    for tool_name, tool in integration.tools.items():
        requested_mode = saved.tools.get(tool_name, "disabled")
        access_mode = clamp_access_mode(requested_mode, tool.maxAccessMode)
        tools[tool_name] = tool.model_copy(
            update={"accessMode": access_mode, "enabled": access_mode != "disabled"}
        )
    return integration.model_copy(update={"enabled": saved.enabled, "tools": tools})


def profiled_integrations(
    owner: str, profile: PermissionProfile | None
) -> list[IntegrationSpec]:
    return [
        apply_permission_profile_to_integration(integration, profile)
        for integration in repository.list_integrations(owner)
    ]


def profiled_integration(
    owner: str, integration_key: str, profile: PermissionProfile | None
) -> IntegrationSpec | None:
    integration = repository.get_integration(owner, integration_key)
    if integration is None:
        return None
    return apply_permission_profile_to_integration(integration, profile)


def create_permission_profile(
    owner: str, request: PermissionProfileCreateRequest
) -> PermissionProfile:
    name = trimmed_profile_name(request)
    if repository.permission_profile_name_exists(owner, name):
        raise HTTPException(
            status_code=409,
            detail=f"A permission profile named '{name}' already exists.",
        )
    source_profile_id = (request.sourceProfileId or "").strip()
    if request.snapshot is not None:
        snapshot = normalize_permission_snapshot(owner, request.snapshot)
    elif source_profile_id:
        source_profile = repository.get_permission_profile(owner, source_profile_id)
        if source_profile is None:
            raise HTTPException(status_code=404, detail="Permission profile not found.")
        snapshot = source_profile.snapshot
    else:
        snapshot = current_permission_snapshot(owner)
    return repository.save_permission_profile(
        owner,
        PermissionProfile(
            ownerId=owner,
            name=name,
            snapshot=snapshot,
        ),
    )


def update_permission_profile(owner: str, profile_id: str) -> PermissionProfile:
    profile = repository.get_permission_profile(owner, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Permission profile not found.")
    updated = profile.model_copy(
        update={"snapshot": current_permission_snapshot(owner), "updatedAt": now_iso()}
    )
    return repository.save_permission_profile(owner, updated)


def normalize_permission_snapshot(
    owner: str, snapshot: PermissionProfileSnapshot
) -> PermissionProfileSnapshot:
    integrations: dict[str, PermissionProfileIntegrationSnapshot] = {}
    for integration_key, saved in snapshot.integrations.items():
        integration = repository.get_integration(owner, integration_key)
        tools: dict[str, ToolAccessMode] = {}
        for tool_name, requested_mode in saved.tools.items():
            if integration is not None:
                tool = integration.tools.get(tool_name)
                if tool is not None:
                    tools[tool_name] = clamp_access_mode(
                        requested_mode, tool.maxAccessMode
                    )
                    continue
            tools[tool_name] = requested_mode
        integrations[integration_key] = PermissionProfileIntegrationSnapshot(
            enabled=saved.enabled,
            tools=tools,
        )
    return PermissionProfileSnapshot(integrations=integrations)


def patch_permission_profile(
    owner: str, profile_id: str, request: PermissionProfilePatchRequest
) -> PermissionProfile:
    profile = repository.get_permission_profile(owner, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Permission profile not found.")
    updated = profile.model_copy(
        update={
            "snapshot": normalize_permission_snapshot(owner, request.snapshot),
            "updatedAt": now_iso(),
        }
    )
    saved = repository.save_permission_profile(owner, updated)
    if profile.name is None:
        apply_permission_profile(owner, saved.id)
    return saved


def delete_permission_profile(owner: str, profile_id: str) -> dict[str, bool]:
    profile = repository.get_permission_profile(owner, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Permission profile not found.")
    if profile.name is None:
        raise HTTPException(
            status_code=400,
            detail="The default permission profile cannot be deleted.",
        )
    if not repository.delete_permission_profile(owner, profile_id):
        raise HTTPException(status_code=404, detail="Permission profile not found.")
    return {"deleted": True}


def apply_permission_profile(
    owner: str, profile_id: str
) -> PermissionProfileApplyResult:
    profile = repository.get_permission_profile(owner, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Permission profile not found.")

    applied_integrations = 0
    applied_tools = 0
    skipped_integrations: list[str] = []
    skipped_tools: list[str] = []

    for integration_key, saved in profile.snapshot.integrations.items():
        integration = repository.get_integration(owner, integration_key)
        if not integration:
            skipped_integrations.append(integration_key)
            continue
        changed = integration.enabled != saved.enabled
        integration.enabled = saved.enabled
        applied_integrations += 1
        for tool_name, requested_mode in saved.tools.items():
            tool = integration.tools.get(tool_name)
            if not tool:
                skipped_tools.append(f"{integration_key}.{tool_name}")
                continue
            access_mode = clamp_access_mode(requested_mode, tool.maxAccessMode)
            integration.tools[tool_name] = tool.model_copy(
                update={"accessMode": access_mode, "enabled": access_mode != "disabled"}
            )
            applied_tools += 1
            changed = True
        if changed:
            repository.save_integration(owner, integration)

    return PermissionProfileApplyResult(
        profile=profile,
        appliedIntegrations=applied_integrations,
        appliedTools=applied_tools,
        skippedIntegrations=skipped_integrations,
        skippedTools=skipped_tools,
    )


def integration_catalog(
    filter: str = "all",
) -> list[dict[str, Any]]:
    """Unified integrations endpoint.

    filter=all — every catalog entry (72): registered, default, and catalog-only.
    filter=registerable — entries that can be managed connectors (47): registered
        plus catalog defaults that pass _is_default_managed_connector.
    filter=enabled — only connectors an admin has explicitly registered (4),
        i.e. repository rows only, no catalog defaults.
    """
    if filter == "enabled":
        managed = list_managed_connectors(include_defaults=False)
        managed_by_slug = {c.slug: c for c in managed}
        catalog_entries: list[dict[str, Any]] = []
        for entry in list_integration_catalog_models():
            slug = entry.id
            connector = managed_by_slug.get(slug)
            if not connector or not connector.enabled:
                continue
            option = entry.connectionOptions[0]
            catalog_entries.append(_catalog_entry(entry, option, connector))
        return catalog_entries

    # filter == "all" or filter == "registerable"
    managed = {connector.slug: connector for connector in list_managed_connectors()}
    registered_slugs = {
        connector.slug for connector in repository.list_managed_connectors()
    }
    catalog_entries = []
    for entry in list_integration_catalog_models():
        option = entry.connectionOptions[0]
        slug = entry.id
        connector = managed.get(slug)
        if filter == "registerable":
            # Include if explicitly registered or passes the default-managed filter.
            if slug not in registered_slugs and not _is_default_managed_connector(
                entry
            ):
                continue
        catalog_entries.append(_catalog_entry(entry, option, connector))
    return catalog_entries


def _catalog_entry(
    entry: IntegrationCatalogEntry,
    option: IntegrationConnectionOption,
    connector: ManagedConnector | None,
) -> dict[str, Any]:
    return {
        "slug": entry.id,
        "name": connector.name if connector else entry.name,
        "description": connector.description if connector else entry.description,
        "categories": connector.categories
        if connector and connector.categories
        else entry.categories or [],
        "popularityRank": entry.popularityRank or 0,
        "appUrl": connector.appUrl if connector else entry.appUrl,
        "docsUrl": connector.docsUrl if connector else entry.docsUrl,
        "logoUrl": connector.logoUrl if connector else entry.logoUrl,
        "iconBg": connector.iconBg if connector else entry.iconBg,
        "iconColor": connector.iconColor if connector else entry.iconColor,
        "connectionDefaults": connector.response()
        if connector
        else connection_defaults_from_option(option).model_dump(exclude_none=True),
        "notes": entry.notes or "",
    }


def _slug_from_integration_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    return slug.strip("-")


def _optional_http_url(value: str, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        return ""
    parsed = urllib.parse.urlparse(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} must be an http(s) URL.",
        )
    return trimmed


def create_integration_request(
    owner: str, payload: IntegrationRequestCreate
) -> dict[str, Any]:
    name = payload.name.strip()
    description = payload.description.strip()
    notes = payload.notes.strip()
    docs_url = _optional_http_url(payload.docsUrl, "docsUrl")
    slug = (payload.slug or "").strip().lower()

    if payload.source == "catalog":
        if not slug:
            raise HTTPException(
                status_code=400,
                detail="slug is required for catalog requests.",
            )
        enabled_slugs = {
            str(entry["slug"]) for entry in integration_catalog(filter="enabled")
        }
        if slug in enabled_slugs:
            raise HTTPException(
                status_code=409,
                detail="This integration is already available in the catalog.",
            )
        catalog_entry = next(
            (
                entry
                for entry in integration_catalog(filter="all")
                if str(entry["slug"]) == slug
            ),
            None,
        )
        if catalog_entry is None:
            raise HTTPException(status_code=404, detail="Unknown catalog integration.")
        name = name or str(catalog_entry.get("name") or slug)
        description = description or str(catalog_entry.get("description") or "")
        docs_url = docs_url or str(
            catalog_entry.get("docsUrl") or catalog_entry.get("appUrl") or ""
        )
    else:
        if not name:
            raise HTTPException(status_code=400, detail="Enter a name.")
        slug = slug or _slug_from_integration_name(name)
        if not slug:
            raise HTTPException(status_code=400, detail="Enter a name.")

    record = IntegrationRequestRecord(
        id=str(uuid.uuid4()),
        source=payload.source,
        slug=slug or None,
        name=name,
        description=description,
        docsUrl=docs_url,
        notes=notes,
        requestedBy=owner,
        status="pending",
    )
    repository.create_notification(
        NotificationRecord(
            id=record.id,
            channel="in_app",
            recipient="admins",
            subject=f"Integration request: {record.name}",
            body=notes or description or f"{owner} requested {record.name}.",
            metadata={
                "type": "integration_request",
                "source": record.source,
                "slug": record.slug,
                "name": record.name,
                "description": record.description,
                "docsUrl": record.docsUrl,
                "notes": record.notes,
                "requestedBy": owner,
                "status": record.status,
            },
            createdAt=record.createdAt,
        )
    )
    return record.model_dump()


def decide_integration_request(
    request_id: str, status: str, reviewer: str
) -> dict[str, Any]:
    try:
        record = repository.decide_integration_request(request_id, status, reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=404, detail="Integration request not found.")
    return record


def approve_access_request(
    owner: str | None,
    request_id: str,
    reviewer: str,
    duration_minutes: int | None = None,
) -> dict[str, Any]:
    request = repository.decide_access_request(owner, request_id, "approved", reviewer)
    if not request:
        raise HTTPException(status_code=404, detail="Access request not found.")
    created = now_iso()
    expires = (
        (
            datetime.now(UTC)
            + timedelta(minutes=duration_minutes or request.requestedMinutes)
        )
        .isoformat()
        .replace("+00:00", "Z")
    )
    grant_owner = owner or repository.access_request_owner_id(request)
    if not grant_owner:
        raise HTTPException(
            status_code=400, detail="Access request is missing an owner."
        )
    return repository.save_temporary_grant(
        {
            "id": secrets.token_urlsafe(18),
            "ownerId": grant_owner,
            "agentId": request.agentId,
            "agentClass": request.agentClass,
            "integrationKey": request.integrationKey,
            "toolName": request.toolName,
            "scopes": request.scopes,
            "grantedBy": reviewer,
            "createdAt": created,
            "expiresAt": expires,
        }
    )


def reject_access_request(
    owner: str | None, request_id: str, reviewer: str
) -> AccessRequest:
    request = repository.decide_access_request(owner, request_id, "rejected", reviewer)
    if not request:
        raise HTTPException(status_code=404, detail="Access request not found.")
    return request


def clamp_access_mode(
    requested: ToolAccessMode, maximum: ToolAccessMode | None
) -> ToolAccessMode:
    if not maximum:
        return requested
    return (
        requested if ACCESS_PRIORITY[requested] <= ACCESS_PRIORITY[maximum] else maximum
    )


def apply_managed_constraints(integration: IntegrationSpec) -> IntegrationSpec:
    slug = (
        integration.config.get("managedConnectorSlug")
        if isinstance(integration.config, dict)
        else None
    )
    connector = get_managed_connector(str(slug)) if slug else None
    if not connector:
        return integration
    tools = connector_tools(connector)
    merged: dict[str, ToolSpec] = {}
    for name, indexed_tool in tools.items():
        existing = integration.tools.get(name)
        requested = existing.accessMode if existing else indexed_tool.accessMode
        access_mode = clamp_access_mode(requested, indexed_tool.accessMode)
        merged[name] = indexed_tool.model_copy(
            update={
                "accessMode": access_mode,
                "enabled": access_mode != "disabled",
                "maxAccessMode": indexed_tool.accessMode,
            }
        )
    return integration.model_copy(
        update={
            "kind": "mcp" if connector.provider == "mcp" else "api",
            "provider": connector.provider,
            "authStrategy": connector.authStrategy,
            "config": {"managedConnectorSlug": connector.slug},
            "tools": merged or integration.tools,
        }
    )


def connector_tools(connector: ManagedConnector) -> dict[str, ToolSpec]:
    source_tools = connector.tools
    if not source_tools and connector.provider == "http" and connector.openApiUrl:
        source_tools = generate_openapi_tools(
            connector.openApiUrl, connector.authStrategy
        )
    return {
        tool.name: ToolSpec(
            name=tool.name,
            description=tool.description,
            defaultScopes=tool.defaultScopes,
            accessMode=tool.accessMode,
            enabled=tool.accessMode != "disabled",
            config={
                **tool.config,
                **(
                    {"request": tool.request.model_dump(exclude_none=True)}
                    if tool.request
                    else {}
                ),
            },
        )
        for tool in source_tools
    }


def managed_tool_from_tool_spec(
    tool: ToolSpec, access_mode: str
) -> ManagedConnectorTool:
    request_data = (
        tool.config.get("request")
        if isinstance(tool.config, dict)
        and isinstance(tool.config.get("request"), dict)
        else None
    )
    request = HttpRequestTemplate.model_validate(request_data) if request_data else None
    config = dict(tool.config)
    config.pop("request", None)
    return ManagedConnectorTool(
        name=tool.name,
        description=tool.description,
        defaultScopes=tool.defaultScopes,
        accessMode=access_mode,  # type: ignore[arg-type]
        request=request,
        config=config,
    )


def _sanitized_log_url(url: str | None) -> str:
    """Return scheme://host[:port]/path for safe logging.

    Managed connector OpenAPI URLs are admin-controlled and may contain signed
    query credentials, URL userinfo (``user:pass@host``), or point at private
    infrastructure; log drains can have broader access than the secret store, so
    never log the raw URL. Strips query, fragment, and userinfo.
    """
    if not url:
        return "none"
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not parsed.scheme or not host:
        return "private"
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}{parsed.path}"


def regenerate_http_openapi_tools(
    connector: ManagedConnector,
    existing_modes: dict[str, str] | None = None,
) -> None:
    """Rebuild an HTTP managed connector's tools from its OpenAPI schema.

    Mutates ``connector.tools`` in place. Preserves previously chosen access
    modes by tool name so re-indexing is non-destructive. Raises the same
    HTTPException as the index endpoint when the connector is missing an
    OpenAPI URL or the schema cannot be loaded.
    """
    if not connector.openApiUrl:
        raise HTTPException(
            status_code=400,
            detail="Managed HTTP connectors need an OpenAPI URL before their tools can be indexed.",
        )
    # Explicit None check: an empty dict means "preserve no modes", not "use
    # the connector's current modes".
    modes = (
        existing_modes
        if existing_modes is not None
        else {tool.name: tool.accessMode for tool in connector.tools}
    )
    generated_tools = generate_openapi_tools(
        connector.openApiUrl, connector.authStrategy
    )
    connector.tools = [
        tool.model_copy(update={"accessMode": modes.get(tool.name, tool.accessMode)})
        for tool in generated_tools
    ]


def index_managed_connector_functions(
    slug: str, owner: str | None = None
) -> dict[str, Any]:
    connector = repository.get_managed_connector(slug)
    if not connector:
        raise HTTPException(status_code=404, detail="Managed connector not found.")
    existing_modes = {tool.name: tool.accessMode for tool in connector.tools}
    provider = connector.provider or ("http" if connector.openApiUrl else "mcp")
    logger.info(
        "indexing managed connector tools: slug=%s provider=%s openApiUrl=%s",
        slug,
        provider,
        _sanitized_log_url(connector.openApiUrl),
    )

    if provider == "http":
        regenerate_http_openapi_tools(connector, existing_modes)
    else:
        server_url = (connector.serverUrl or connector.apiBaseUrl or "").strip()
        if not server_url:
            raise HTTPException(
                status_code=400,
                detail="Managed MCP connectors need a server URL before their tools can be indexed.",
            )
        credentials = (
            repository.get_connection_credentials(owner, connector.slug)
            if owner and connector.authStrategy == "oauth2"
            else {}
        )
        # OAuth2 MCP servers (e.g. Slack's hosted MCP) require a user access
        # token on every request, including the `initialize` handshake used to
        # list tools. Without one the server rejects the call with an opaque
        # JSON-RPC error (Slack returns `missing_token`). Surface a clear,
        # actionable error instead so the admin knows to connect their own
        # account first. Use the connector's credentialLabel for the button
        # name so the message matches the UI the admin actually sees.
        if connector.authStrategy == "oauth2" and not (credentials or {}).get(
            "accessToken"
        ):
            connect_label = (
                str(connector.credentialLabel or "").strip()
                or f"Connect {connector.name}"
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{connect_label} with your own account before indexing "
                    f"{connector.name}'s tools. Open the connector below, click "
                    f'"{connect_label}", complete the authorization, then '
                    f"retry indexing."
                ),
            )
        discovered = discover_mcp_tools(
            server_url, mcp_discovery_headers(connector.authStrategy, credentials or {})
        )
        connector.tools = [
            managed_tool_from_tool_spec(
                tool, existing_modes.get(name, tool.accessMode or "enabled")
            )
            for name, tool in discovered.items()
        ]

    connector.updatedAt = now_iso()
    saved = repository.save_managed_connector(connector)
    with_input_schema = sum(1 for tool in saved.tools if tool.config.get("inputSchema"))
    logger.info(
        "indexed managed connector tools: slug=%s tools=%d with_input_schema=%d",
        slug,
        len(saved.tools),
        with_input_schema,
    )
    return saved.response()


def infer_auth_strategy(credentials: dict[str, Any]) -> str:
    if credentials.get("accessToken"):
        return "bearer"
    if credentials.get("secret"):
        return "api_key"
    if credentials.get("username") and credentials.get("password"):
        return "basic"
    return "none"


def managed_connector_credentials(
    connector: ManagedConnector, credentials: dict[str, Any]
) -> dict[str, Any]:
    if connector.authStrategy in {"none", "oauth2"}:
        return {}
    if connector.authStrategy == "api_key":
        secret = str(credentials.get("secret") or "").strip()
        if not secret:
            raise HTTPException(
                status_code=400,
                detail=f"{connector.credentialLabel} is required to use the {connector.name} connector.",
            )
        return {
            "headerName": str(
                credentials.get("headerName")
                or connector.apiKeyHeaderName
                or "X-API-Key"
            ),
            "secret": secret,
        }
    if connector.authStrategy == "bearer":
        access_token = str(credentials.get("accessToken") or "").strip()
        if not access_token:
            raise HTTPException(
                status_code=400,
                detail=f"{connector.credentialLabel} is required to use the {connector.name} connector.",
            )
        return {"accessToken": access_token}
    if connector.authStrategy == "basic":
        username = str(credentials.get("username") or "").strip()
        password = str(credentials.get("password") or "").strip()
        if not username or not password:
            raise HTTPException(
                status_code=400,
                detail=f"{connector.credentialLabel} is required to use the {connector.name} connector.",
            )
        return {"username": username, "password": password}
    raise HTTPException(
        status_code=400,
        detail=f"Managed connector '{connector.slug}' uses unsupported auth strategy '{connector.authStrategy}'.",
    )


def mcp_discovery_headers(
    auth_strategy: str, credentials: dict[str, Any]
) -> dict[str, str]:
    integration = IntegrationSpec(
        key="discovery",
        name="Discovery",
        kind="mcp",
        provider="mcp",
        authStrategy=auth_strategy,
        credentials=credentials,
    )
    return http_auth_headers(integration, credentials)


def discover_mcp_tools(
    server_url: str, headers: dict[str, str], *, timeout: float = 20
) -> dict[str, ToolSpec]:
    validated_server_url = validate_external_url(server_url, purpose="MCP server")
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        initialized, session_id = post_mcp_message(
            client,
            validated_server_url,
            headers,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": MCP_CLIENT_INFO,
                },
            },
            None,
            None,
            1,
        )
        protocol_version = str(
            (initialized or {}).get("protocolVersion") or MCP_PROTOCOL_VERSION
        )
        post_mcp_message(
            client,
            validated_server_url,
            headers,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id,
            protocol_version,
            None,
        )
        result, _ = post_mcp_message(
            client,
            validated_server_url,
            headers,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id,
            protocol_version,
            2,
        )
    tools = (result or {}).get("tools") or []
    discovered: dict[str, ToolSpec] = {}
    for item in tools:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item["name"])
        discovered[name] = ToolSpec(
            name=name,
            description=str(item.get("description") or item.get("title") or ""),
            config={"toolName": name, "inputSchema": item.get("inputSchema") or {}},
        )
    return discovered


def managed_mcp_discovery_credentials(
    owner: str,
    integration_key: str,
    connector: ManagedConnector,
    credentials: dict[str, Any],
    connection_id: str | None = None,
) -> dict[str, Any]:
    if connector.authStrategy == "oauth2":
        saved_credentials = repository.get_connection_credentials(
            owner, integration_key, connection_id
        )
        if not saved_credentials:
            raise HTTPException(
                status_code=403,
                detail=f"Connect {connector.name} for this user before discovering tools.",
            )
        return saved_credentials
    return managed_connector_credentials(connector, credentials)


def discover_managed_mcp_tools_for_owner(
    owner: str,
    integration_key: str,
    connector: ManagedConnector,
    credentials: dict[str, Any],
    *,
    timeout: float | None = None,
    connection_id: str | None = None,
) -> dict[str, ToolSpec]:
    server_url = str(connector.serverUrl or connector.apiBaseUrl or "").strip()
    if not server_url:
        raise HTTPException(
            status_code=400,
            detail=f"Managed MCP connector '{connector.slug}' is missing a server URL.",
        )
    discovery_credentials = managed_mcp_discovery_credentials(
        owner, integration_key, connector, credentials, connection_id
    )
    return discover_mcp_tools_with_transport_order(
        server_url,
        mcp_discovery_headers(connector.authStrategy, discovery_credentials),
        timeout=timeout,
    )


def backfill_oauth_mcp_tools_after_connection(
    owner: str,
    integration_key: str,
    connector: ManagedConnector,
) -> None:
    """Best-effort: after an OAuth connection is saved, live-discover the
    connector's MCP tools with the user's new credentials and merge them into
    any already-saved per-user integration so the full tool set is visible
    immediately without a manual "Refresh indexed tools" step.

    Only applies to MCP connectors (HTTP OAuth connectors generate tools from
    their OpenAPI URL on save/index, so there is no fuller set to discover
    here). Failures are logged and swallowed so the OAuth callback never
    breaks because of a discovery hiccup.
    """
    if connector.provider != "mcp":
        return
    integration = repository.get_integration(owner, integration_key)
    if not integration:
        return
    try:
        discovered = discover_managed_mcp_tools_for_owner(
            owner, integration_key, connector, {}, timeout=5
        )
    except Exception as exc:  # noqa: BLE001 - best-effort, never fail callback
        logger.warning(
            "post-oauth mcp tool discovery failed: owner=%s connector=%s error=%s",
            owner,
            connector.slug,
            exc,
        )
        return
    if not discovered:
        return
    existing_tools = dict(integration.tools)
    changed = False
    added_count = 0
    for name, tool in discovered.items():
        existing = existing_tools.get(name)
        if existing:
            # Preserve the user's chosen access mode; only fill missing pieces.
            continue
        existing_tools[name] = tool
        changed = True
        added_count += 1
    if not changed:
        return
    updated = integration.model_copy(update={"tools": existing_tools})
    repository.save_integration(owner, apply_managed_constraints(updated))
    logger.info(
        "post-oauth mcp tool backfill: owner=%s connector=%s added=%d total=%d",
        owner,
        connector.slug,
        added_count,
        len(existing_tools),
    )


def discovered_integration(
    payload: IntegrationDiscoveryRequest,
    owner: str,
    *,
    allow_catalog_defaults: bool = False,
) -> dict[str, Any]:
    config = payload.config
    credentials = payload.credentials
    slug = (
        payload.managedConnectorSlug
        or config.get("managedConnectorSlug")
        or payload.key
    )
    stored_connector = repository.get_managed_connector(str(slug)) if slug else None
    if stored_connector and not stored_connector.enabled:
        raise HTTPException(
            status_code=404, detail=f"Unknown managed connector '{slug}'."
        )
    # User-facing gate: a managed connector is only installable by users if an
    # admin has explicitly added it (i.e. it exists in the repository). Catalog
    # defaults are NOT installable by users without admin registration — this
    # matches the filter=enabled gate on /api/integrations so the list and the
    # install path share one authorization boundary. The admin discover path
    # passes allow_catalog_defaults=True so admins can preview defaults.
    connector = stored_connector
    if allow_catalog_defaults and not connector:
        connector = default_managed_connector(str(slug) if slug else "")
    if slug and not connector and payload.is_managed:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Managed connector '{slug}' is not available. An admin must "
                "add it before users can connect it."
            ),
        )
    if connector:
        integration_key = str(payload.key or connector.slug)
        if connector.authStrategy == "oauth2" and not connector.oauthConfigured:
            raise HTTPException(
                status_code=403,
                detail=f"{connector.name} needs an admin to configure OAuth before users can add it.",
            )
        configured_connection_id = str(config.get("connectionId") or "").strip()
        configured_resource_id = str(config.get("resourceId") or "").strip()
        connection: OAuthConnection | None = None
        if connector.authStrategy == "oauth2":
            stored_connection = (
                repository.get_connection_by_id(owner, configured_connection_id)
                if configured_connection_id
                else repository.get_connection(owner, integration_key)
            )
            if not stored_connection:
                raise HTTPException(
                    status_code=403,
                    detail=f"Connect {connector.name} for this user before discovering tools.",
                )
            connection = OAuthConnection.model_validate(stored_connection)
            connection_provider = connection.provider
            is_legacy_transport_provider = (
                connection_provider == connector.provider
                and connection.integrationKey == integration_key
            )
            if (
                connection_provider != connector.slug
                and not is_legacy_transport_provider
            ):
                raise HTTPException(
                    status_code=400,
                    detail="The selected connection belongs to a different connector.",
                )
            if configured_resource_id and not any(
                resource.id == configured_resource_id
                for resource in connection.resources
            ):
                raise HTTPException(
                    status_code=400,
                    detail="The selected external resource does not belong to this connection.",
                )
            configured_connection_id = connection.connectionId
        resolved_credentials = managed_connector_credentials(connector, credentials)
        tools = connector_tools(connector)
        if connector.provider == "mcp" and not tools:
            tools = discover_managed_mcp_tools_for_owner(
                owner,
                integration_key,
                connector,
                credentials,
                connection_id=configured_connection_id or None,
            )
        integration_config = {"managedConnectorSlug": connector.slug}
        if configured_connection_id:
            integration_config["connectionId"] = configured_connection_id
        if configured_resource_id:
            integration_config["resourceId"] = configured_resource_id
        return {
            "key": integration_key,
            "name": payload.name or connector.name,
            "kind": "mcp" if connector.provider == "mcp" else "api",
            "provider": connector.provider,
            "authStrategy": connector.authStrategy,
            "enabled": True,
            "fineGrainedPermissions": True,
            "config": integration_config,
            "credentials": resolved_credentials,
            "tools": {name: tool.model_dump() for name, tool in tools.items()},
        }
    if payload.kind != "mcp":
        raise HTTPException(
            status_code=400,
            detail="Only managed connectors and raw MCP discovery are supported.",
        )
    server_url = str(config.get("serverUrl") or "").strip()
    if not server_url:
        raise HTTPException(
            status_code=400, detail="MCP discovery requires config.serverUrl."
        )
    auth_strategy = infer_auth_strategy(credentials)
    tools = discover_mcp_tools_with_transport_order(
        server_url, mcp_discovery_headers(auth_strategy, credentials), config
    )
    return {
        "key": payload.key or "custom",
        "name": payload.name or payload.key or "Custom integration",
        "kind": "mcp",
        "provider": "mcp",
        "authStrategy": auth_strategy,
        "enabled": True,
        "fineGrainedPermissions": True,
        "config": {"serverUrl": server_url},
        "credentials": credentials,
        "tools": {name: tool.model_dump() for name, tool in tools.items()},
    }


def bind_oauth_integration_connection(
    owner: str, integration: IntegrationSpec
) -> IntegrationSpec:
    if integration.authStrategy != "oauth2":
        return integration
    config = dict(integration.config)
    configured_connection_id = str(config.get("connectionId") or "").strip()
    stored_connection = (
        repository.get_connection_by_id(owner, configured_connection_id)
        if configured_connection_id
        else repository.get_connection(owner, integration.key)
    )
    if not stored_connection:
        if not configured_connection_id and not config.get("resourceId"):
            return integration
        raise HTTPException(
            status_code=403,
            detail=f"Connect {integration.name} before saving this integration.",
        )
    connection = OAuthConnection.model_validate(stored_connection)
    config["connectionId"] = connection.connectionId
    configured_resource_id = str(config.get("resourceId") or "").strip()
    resources = connection.resources
    if configured_resource_id and not any(
        resource.id == configured_resource_id for resource in resources
    ):
        raise HTTPException(
            status_code=400,
            detail="The selected external resource does not belong to this connection.",
        )
    connector_slug = str(config.get("managedConnectorSlug") or "").strip()
    connector = get_managed_connector(connector_slug) if connector_slug else None
    connection_provider = connection.provider
    is_legacy_transport_provider = bool(
        connector
        and connection_provider == connector.provider
        and connection.integrationKey == integration.key
    )
    if (
        connector
        and connection_provider != connector.slug
        and not is_legacy_transport_provider
    ):
        raise HTTPException(
            status_code=400,
            detail="The selected connection belongs to a different connector.",
        )
    selection_mode = (
        connector.connectionModel.selectionMode
        if connector and connector.connectionModel
        else None
    )
    if selection_mode == "post_auth" and not configured_resource_id:
        raise HTTPException(
            status_code=400,
            detail="Select an external resource before saving this integration.",
        )
    if (
        not configured_resource_id
        and selection_mode == "automatic"
        and len(resources) == 1
    ):
        config["resourceId"] = resources[0].id
    return integration.model_copy(update={"config": config})


async def json_body(request: Request) -> Any:
    return (
        await request.json()
        if request.headers.get("content-length") not in (None, "0")
        else {}
    )


def dispatch_fastapi_request(request: Request, body: Any):
    params = request.path_params
    path = request.scope["route"].path
    method = request.method

    if path == "/api/oauth/{provider}/callback":
        provider = params["provider"]
        state = request.query_params.get("state", "")
        preview_target = oauth_proxy_callback_target(request, state)
        if preview_target:
            callback_query = urllib.parse.urlencode(
                list(request.query_params.multi_items())
            )
            return RedirectResponse(
                f"{preview_target}{public_api_path(f'/api/oauth/{provider}/callback')}?{callback_query}"
            )
        code = request.query_params.get("code", "")
        error = request.query_params.get("error")
        if error:
            redirect_to = consume_oauth_redirect_target(provider, state)
            return RedirectResponse(
                build_oauth_callback_redirect(
                    redirect_to,
                    oauth_status="error",
                    oauth_provider=provider,
                    oauth_error=error,
                )
            )
        connection = exchange_oauth_code(provider, state, code)
        # Best-effort: populate the full MCP tool set now that the user's
        # OAuth credentials exist, so the integration doesn't show 1/no tools
        # until a manual "Refresh indexed tools".
        connector = get_managed_connector(provider)
        if connector:
            backfill_oauth_mcp_tools_after_connection(
                str(connection.get("ownerId") or ""),
                str(connection.get("integrationKey") or provider),
                connector,
            )
        return RedirectResponse(
            build_oauth_callback_redirect(
                str(connection.get("redirect_to") or ""),
                oauth_status="connected",
                oauth_provider=provider,
                integration_key=str(connection["integrationKey"]),
            )
        )

    if path == "/api/cron/expire-grants":
        try:
            secret = get_default_config().require_cron_secret()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if request.headers.get("authorization") != f"Bearer {secret}":
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {"purged": repository.purge_expired_grants()}

    owner: str | None = None
    user_api_is_admin = False
    if requires_dashboard_session(path):
        session_user = authorize_dashboard_user(request)
        owner = session_user.owner_id
        user_api_is_admin = session_user.is_admin
    active_permission_profile: PermissionProfile | None = None
    if path.startswith("/api/agent") or path.startswith("/api/context"):
        agent_record = authorized_agent_record(request, "call /api/agent")
        owner = str(agent_record["ownerId"])
        active_permission_profile = agent_permission_profile(agent_record)
    if path == "/api/mcp":
        agent_record = authorized_agent_record(request, "connect to MCP")
        owner = str(agent_record["ownerId"])
        active_permission_profile = agent_permission_profile(agent_record)
    if path.startswith("/api/user/") and path not in {
        "/api/user/key",
        "/api/user/agent-key",
    }:
        owner, user_api_is_admin = authorize_user_api_owner(request)
    if path.startswith("/api/case-by-case-approvals"):
        owner = authorize_user_api_owner_or_session(request)
        user_api_is_admin = is_admin_owner(owner)
    if path.startswith("/api/admin/") and path != "/api/admin/key":
        owner = authorize_admin_api_owner(request)
        user_api_is_admin = True
    if owner is None:
        raise HTTPException(status_code=401, detail="Authentication is required.")

    if path == "/api/permission-profiles":
        ensure_default_permission_profile(owner, refresh=True)
        if method == "GET":
            return repository.list_permission_profiles(owner)
        return create_permission_profile(
            owner, PermissionProfileCreateRequest.model_validate(body)
        )

    if path == "/api/permission-profiles/{profileId}/update":
        return update_permission_profile(owner, params["profileId"])

    if path == "/api/permission-profiles/{profileId}":
        if method == "DELETE":
            return delete_permission_profile(owner, params["profileId"])
        if method == "PATCH":
            return patch_permission_profile(
                owner,
                params["profileId"],
                PermissionProfilePatchRequest.model_validate(body),
            )
        raise HTTPException(status_code=405, detail="Method not allowed.")

    if path == "/api/permission-profiles/{profileId}/agent-key":
        return rotate_permission_profile_agent_key(owner, params["profileId"])

    if path == "/api/permission-profiles/{profileId}/load":
        result = apply_permission_profile(owner, params["profileId"])
        sync_default_permission_profile(owner)
        return result

    if path in {"/api/agent/openapi", "/api/context/openapi"}:
        return agent_openapi(owner, request, active_permission_profile)
    if path == "/api/user/openapi":
        return user_openapi(request, user_api_is_admin)
    if path == "/api/admin/openapi":
        return admin_openapi(request)
    if path == "/api/admin/modify-integrations/openapi":
        return managed_openapi(request)

    if path in {"/api/agent/skill", "/api/context/skill"}:
        lines = ["# OpenHands Integrations Hub", "", "Available tools:"]
        for integration in profiled_integrations(owner, active_permission_profile):
            for tool in integration.tools.values():
                if tool.enabled and tool.accessMode != "disabled":
                    lines.append(
                        f"- `{integration.key}.{tool.name}` — {tool.description or 'No description'}"
                    )
        return PlainTextResponse("\n".join(lines) + "\n")

    if path == "/api/mcp":
        if method == "GET":
            return StreamingResponse(
                iter([": connected\n\n"]), media_type="text/event-stream"
            )
        if method == "DELETE":
            return {"terminated": True}
        return mcp_call(
            owner,
            body if isinstance(body, dict) else {},
            case_by_case_approval_url(request),
            active_permission_profile,
        )

    if path in {"/api/oauth/connections", "/api/user/connections"}:
        return repository.list_connections(owner)

    if path == "/api/admin/connections":
        return repository.list_connections(filters=admin_connection_filters(request))

    if path == "/api/notifications":
        require_app_admin(owner, ADMIN_NOTIFICATIONS_DETAIL, user_api_is_admin)
        return repository.list_notifications()

    if path == "/api/admin/notifications":
        return repository.list_notifications()

    if path == "/api/oauth/{provider}/start":
        accepts_json = "application/json" in request.headers.get("accept", "")
        return start_oauth_redirect(
            request,
            params["provider"],
            owner,
            response_as_json=accepts_json
            or request.query_params.get("response") == "json",
        )

    if path == "/api/integrations" or path == "/api/user/integrations":
        if method == "GET":
            # The /api/integrations GET endpoint returns the user's installed
            # integrations. When the ?filter query param is present, it returns
            # catalog entries instead (all/registerable/enabled).
            filter_value = (request.query_params.get("filter") or "").strip()
            if filter_value:
                if filter_value not in {"all", "registerable", "enabled"}:
                    raise HTTPException(
                        status_code=400,
                        detail="filter must be one of: all, registerable, enabled",
                    )
                return integration_catalog(filter=filter_value)
            return repository.list_integrations(owner)
        integration = IntegrationSpec.model_validate(body)
        if integration.authStrategy == "oauth2" and integration.credentials:
            raise HTTPException(
                status_code=400,
                detail="OAuth integrations cannot store credentials directly. Save per-user OAuth connections instead.",
            )
        assert_user_can_save_integration(owner, integration, user_api_is_admin)
        integration = bind_oauth_integration_connection(
            owner, apply_managed_constraints(integration)
        )
        saved_integration = repository.save_integration(owner, integration)
        sync_default_permission_profile(owner)
        return saved_integration

    if (
        path == "/api/integrations/{integrationKey}"
        or path == "/api/user/integrations/{integrationKey}"
    ):
        if not repository.delete_integration(owner, params["integrationKey"]):
            raise HTTPException(status_code=404, detail="Integration not found.")
        sync_default_permission_profile(owner)
        return {"deleted": True, "integrationKey": params["integrationKey"]}

    if path == "/api/integrations/{integrationKey}/toggle":
        integration = repository.get_integration(owner, params["integrationKey"])
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found.")
        toggle = IntegrationToggleRequest.model_validate(body)
        integration.enabled = (
            toggle.enabled if toggle.enabled is not None else not integration.enabled
        )
        saved_integration = repository.save_integration(
            owner, apply_managed_constraints(integration)
        )
        sync_default_permission_profile(owner)
        return saved_integration

    if path == "/api/integrations/{integrationKey}/tools":
        tool = repository.save_tool(
            owner, params["integrationKey"], ToolSpec.model_validate(body)
        )
        if not tool:
            raise HTTPException(status_code=404, detail="Integration not found.")
        sync_default_permission_profile(owner)
        return tool

    if path in {
        "/api/integrations/{integrationKey}/tools/{toolName}",
        "/api/user/integrations/{integrationKey}/tools/{toolName}",
    }:
        patch = ToolAccessPatch.model_validate(body)
        try:
            tool = repository.update_tool(
                owner,
                params["integrationKey"],
                params["toolName"],
                patch,
            )
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found.")
        sync_default_permission_profile(owner)
        return tool

    if path == "/api/integrations/{integrationKey}/tools/{toolName}/toggle":
        toggle = IntegrationToggleRequest.model_validate(body)
        access_mode = "enabled" if toggle.enabled is not False else "disabled"
        try:
            tool = repository.update_tool(
                owner,
                params["integrationKey"],
                params["toolName"],
                ToolAccessPatch(accessMode=access_mode),
            )
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found.")
        sync_default_permission_profile(owner)
        return tool

    if path in {"/api/integrations/discover", "/api/admin/integrations/discover"}:
        discovery_request = IntegrationDiscoveryRequest.model_validate(body)
        if path == "/api/integrations/discover":
            assert_user_can_discover_integration(
                owner, discovery_request, user_api_is_admin
            )
        # The admin discover path may preview catalog-default connectors that
        # have not been admin-added yet; the user path is gated to admin-added.
        return discovered_integration(
            discovery_request,
            owner,
            allow_catalog_defaults=path.startswith("/api/admin/"),
        )

    if path == "/api/integrations/requests":
        if method != "POST":
            raise HTTPException(status_code=405, detail="Method not allowed.")
        return create_integration_request(
            owner, IntegrationRequestCreate.model_validate(body)
        )

    if path == "/api/admin/integration-requests":
        return repository.list_integration_requests()

    if path == "/api/admin/integration-requests/{requestId}/add":
        return decide_integration_request(params["requestId"], "added", owner)

    if path == "/api/admin/integration-requests/{requestId}/dismiss":
        return decide_integration_request(params["requestId"], "dismissed", owner)

    if path == "/api/integrations/disable-unused":
        result = disable_unused_tools(
            owner, DisableUnusedToolsRequest.model_validate(body)
        )
        sync_default_permission_profile(owner)
        return result

    if path == "/api/admin/modify-integrations":
        if method == "GET":
            return [connector.response() for connector in list_managed_connectors()]
        require_app_admin(owner, ADMIN_CONNECTOR_MANAGEMENT_DETAIL, user_api_is_admin)
        connector = ensure_managed_oauth_client(ManagedConnector.model_validate(body))
        # Regenerate HTTP tools on save so the persisted tool set reflects the
        # schema (e.g. Notion's 44 endpoints), not whatever the client sent.
        # Access modes merge in client-wins order: stored-by-name -> client-submitted.
        if connector.provider == "http" and connector.openApiUrl:
            previous = repository.get_managed_connector(connector.slug)
            existing_modes = {
                tool.name: tool.accessMode
                for tool in (previous.tools if previous else [])
            }
            existing_modes.update(
                {tool.name: tool.accessMode for tool in connector.tools}
            )
            regenerate_http_openapi_tools(connector, existing_modes)
        return repository.save_managed_connector(connector).response()

    if path == "/api/admin/modify-integrations/{slug}":
        require_app_admin(owner, ADMIN_CONNECTOR_MANAGEMENT_DETAIL, user_api_is_admin)
        if not repository.delete_managed_connector(params["slug"]):
            raise HTTPException(status_code=404, detail="Managed connector not found.")
        repository.delete_connections_by_integration_key(params["slug"])
        return {"deleted": True, "slug": params["slug"]}

    if path == "/api/admin/modify-integrations/{slug}/functions":
        require_app_admin(owner, ADMIN_CONNECTOR_MANAGEMENT_DETAIL, user_api_is_admin)
        return index_managed_connector_functions(params["slug"], owner)

    if path in {
        "/api/agent/{integrationKey}/{toolName}",
        "/api/context/{integrationKey}/{toolName}",
    }:
        integration = profiled_integration(
            owner, params["integrationKey"], active_permission_profile
        )
        payload = AgentInvocationRequest.model_validate(body)
        if not integration or not integration.enabled:
            raise HTTPException(
                status_code=404, detail="Integration not found or disabled."
            )
        tool = integration.tools.get(params["toolName"])
        if not tool or not tool.enabled or tool.accessMode == "disabled":
            raise HTTPException(status_code=403, detail="Tool is disabled.")
        data = invoke_enabled_tool(
            owner,
            integration,
            tool,
            payload.scopes,
            payload.payload,
            payload.agentId,
            payload.agentClass,
            case_by_case_approval_url(request),
            active_permission_profile is None,
        )
        return {
            "integration": integration.key,
            "tool": tool.name,
            "provider": integration.provider,
            "kind": integration.kind,
            "authStrategy": integration.authStrategy,
            "decision": {
                "allowed": True,
                "reason": "Tool is enabled.",
                "effectiveScopes": payload.scopes,
            },
            "data": data,
        }

    if path in {"/api/case-by-case-approvals", "/api/user/access-requests"}:
        return repository.list_access_requests(owner)

    if path == "/api/admin/access-requests":
        return repository.list_access_requests(None)

    if path.endswith("/approve") or path.endswith("/reject"):
        request_id = params["requestId"]
        request_owner = None if path.startswith("/api/admin/") else owner
        if path.endswith("/approve"):
            approval = AccessRequestApprovalRequest.model_validate(body)
            return approve_access_request(
                request_owner, request_id, owner, approval.durationMinutes
            )
        return reject_access_request(request_owner, request_id, owner)

    if path == "/api/user/key":
        bucket, prefix = repository.user_keys, "clu"
    elif path == "/api/user/agent-key":
        ensure_default_permission_profile(owner, refresh=True)
        bucket, prefix = repository.agent_keys, "cla"
    elif path == "/api/admin/key":
        bucket, prefix = repository.admin_keys, "clad"
    else:
        bucket = prefix = None
    if bucket is not None:
        key_kind = (
            "agent" if prefix == "cla" else "user" if prefix == "clu" else "admin"
        )
        if key_kind == "admin":
            require_app_admin(owner, ADMIN_KEY_MANAGEMENT_DETAIL, user_api_is_admin)
        if method == "DELETE":
            return {"revoked": repository.delete_key(owner, key_kind)}
        if method == "POST":
            value = repository.rotate_key(bucket, owner, prefix)  # type: ignore[arg-type]
            data = repository.save_key(owner, key_kind, value)
        else:
            data = repository.get_key(owner, key_kind)
        if key_kind == "agent":
            key = data.get("key") or {}
            return {
                "ownerId": owner,
                "apiKey": data["apiKey"],
                "createdAt": key.get("createdAt") or now_iso(),
                "permissionProfileId": data.get("permissionProfileId"),
                "updatedAt": key.get("updatedAt") or now_iso(),
            }
        if method == "POST":
            return data
        return {"hasKey": data["hasKey"], "key": data["key"], "apiKey": data["apiKey"]}

    if path == "/api/admin/overview":
        return repository.overview()

    raise HTTPException(
        status_code=501,
        detail=f"FastAPI handler for {method} {path} is not implemented.",
    )


def auth_session_response(request: Request):
    try:
        user = openhands_user_from_request(request)
    except AuthUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="OpenHands Cloud is temporarily unavailable. Please retry.",
        ) from exc
    if not user:
        raise HTTPException(status_code=401, detail="Sign in with OpenHands Cloud.")
    owner = user.owner_id
    user_payload = {
        "id": user.id,
        "name": user.email.lower(),
        "email": user.email.lower(),
        "ownerId": owner,
        "orgId": user.normalized_org_id,
        "orgName": user.org_name,
        "role": user.role,
        "permissions": list(user.permissions),
        "isAdmin": user.is_admin,
    }

    return {
        "user": user_payload,
        "expires": "2099-01-01T00:00:00.000Z",
    }


async def fastapi_handler(request: Request):
    path = request.scope["route"].path
    if path in {"/api/health", "/api/live"}:
        return {"status": "ok", "backend": "fastapi"}
    if path == "/api/ready":
        ready = await request_executors(request.app).readiness(
            repository.database_ready
        )
        if ready is None:
            logger.warning("database readiness check timed out")
        if not ready:
            raise HTTPException(status_code=503, detail="Database is not ready.")
        return {"status": "ready", "backend": "fastapi"}
    body = (
        await json_body(request) if request.method in {"POST", "PATCH", "PUT"} else {}
    )
    return await run_blocking(request, dispatch_fastapi_request, request, body)


async def auth_handler(request: Request):
    cfg = get_default_config()
    if request.url.path.endswith("/config"):
        return {
            "openhandsBaseUrl": cfg.openhands_base_url or "",
            "posthogClientKey": cfg.posthog_client_key,
        }
    return await run_blocking(request, auth_session_response, request)


for route_method, route_path in ROUTES:
    app.add_api_route(
        route_path,
        fastapi_handler,
        methods=[route_method],
        name=f"{route_method} {route_path}",
    )

for health_path in ["/api/health", "/api/live", "/api/ready"]:
    app.add_api_route(
        health_path,
        fastapi_handler,
        methods=["GET"],
        name=f"GET {health_path}",
    )

for auth_path in ["/api/auth/config", "/api/auth/session"]:
    app.add_api_route(
        auth_path, auth_handler, methods=["GET"], name=f"AUTH {auth_path}"
    )

# Mount static files for SPA serving (K8s/Docker mode only).
# This must be called AFTER all API routes are registered, as it includes
# a catch-all route for SPA client-side routing.
# When STATIC_DIR is not set, this is a no-op.
mount_static_files(app)
