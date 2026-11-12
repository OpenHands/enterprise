"""Offset pagination shared by the instance-wide admin list endpoints.

An instance can hold hundreds of thousands of users and organizations, so no
admin list endpoint may return an unbounded result. ``page_id`` is the offset,
as a string, to match ``GET /api/admin/users`` and the OAuth provider links.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Query, status
from pydantic import BaseModel

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

PageIdQuery = Annotated[
    str | None,
    Query(title='Optional offset (as a string) from a previous page'),
]
LimitQuery = Annotated[
    int,
    Query(title='The max number of results in the page', gt=0, le=MAX_PAGE_SIZE),
]
SearchQuery = Annotated[
    str | None,
    Query(
        title='Filter by partial, case-insensitive match', min_length=1, max_length=255
    ),
]


class PageMeta(BaseModel):
    """Position of a page within the full result set."""

    total: int
    current_page: int
    per_page: int


def parse_page_offset(page_id: str | None) -> int:
    if page_id is None:
        return 0
    try:
        offset = int(page_id)
    except ValueError:
        offset = -1
    if offset < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid page_id format',
        )
    return offset


def page_meta(total: int, offset: int, limit: int) -> dict[str, int]:
    return {
        'total': total,
        'current_page': (offset // limit) + 1,
        'per_page': limit,
    }
