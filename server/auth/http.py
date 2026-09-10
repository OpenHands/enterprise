"""HTTP validation that never returns rejected credential values."""

from functools import wraps

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class AuthValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        @wraps(handler)
        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default error details contain the unparsed input.
                raise HTTPException(422, 'Invalid authentication request.') from None

        return validated
