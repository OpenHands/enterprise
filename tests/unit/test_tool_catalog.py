import pytest
from pydantic import BaseModel

import openhands.sdk.tool.registry as registry
from server.routes.tool_catalog import get_tool_catalog


class _Entry(BaseModel):
    name: str
    usable: bool


@pytest.mark.asyncio
async def test_catalog_skips_usability_probes(monkeypatch):
    calls = []

    def list_tool_catalog(*, check_usable=True):
        calls.append(check_usable)
        return [_Entry(name='terminal', usable=True)]

    monkeypatch.setattr(registry, 'list_tool_catalog', list_tool_catalog)

    response = await get_tool_catalog(user_id='user')

    assert calls == [False]
    assert response == {'tools': [{'name': 'terminal', 'usable': True}]}


@pytest.mark.asyncio
async def test_catalog_lists_registered_tools():
    import openhands.agent_server.tool_router  # noqa: F401

    names = {t['name'] for t in (await get_tool_catalog(user_id='user'))['tools']}

    assert {'terminal', 'file_editor', 'task_tool_set', 'switch_llm'} <= names
