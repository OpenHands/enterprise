import pytest
from fastapi import HTTPException
from pydantic import BaseModel

import openhands.sdk.tool.registry as registry
from server.routes.tool_catalog import get_tool_catalog


class _Entry(BaseModel):
    name: str
    usable: bool


@pytest.mark.asyncio
async def test_catalog_marks_every_tool_usable_in_cloud_sandboxes(monkeypatch):
    monkeypatch.setattr(
        registry,
        'list_tool_catalog',
        lambda: [
            _Entry(name='terminal', usable=True),
            _Entry(name='browser_tool_set', usable=False),
        ],
        raising=False,
    )

    response = await get_tool_catalog(user_id='user')

    assert response == {
        'tools': [
            {'name': 'terminal', 'usable': True},
            {'name': 'browser_tool_set', 'usable': True},
        ]
    }


@pytest.mark.asyncio
async def test_catalog_is_not_found_on_an_sdk_without_one(monkeypatch):
    monkeypatch.delattr(registry, 'list_tool_catalog', raising=False)

    with pytest.raises(HTTPException) as excinfo:
        await get_tool_catalog(user_id='user')

    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_catalog_skips_usability_probes_when_the_sdk_allows(monkeypatch):
    calls = []

    def list_tool_catalog(*, check_usable=True):
        calls.append(check_usable)
        return [_Entry(name='terminal', usable=False)]

    monkeypatch.setattr(registry, 'list_tool_catalog', list_tool_catalog, raising=False)

    response = await get_tool_catalog(user_id='user')

    assert calls == [False]
    assert response == {'tools': [{'name': 'terminal', 'usable': True}]}
