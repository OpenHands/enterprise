"""Validation errors for write-only organization-secret APIs."""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class OrgSecretValidationRoute(APIRoute):
    """Avoid reflecting raw credential inputs or error contexts in HTTP 422s."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    422,
                    detail=[
                        {
                            'type': error['type'],
                            'loc': ['body']
                            if error['type'] == 'extra_forbidden'
                            else error['loc'],
                            'msg': error['msg'],
                        }
                        for error in exc.errors()
                    ],
                ) from None

        return handle
