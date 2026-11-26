"""The versioned JSON envelope shared by organization, team and member rows."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import TYPE_CHECKING, Any, Final, Literal, overload

from pydantic import BaseModel

from openhands.app_server.settings.scoped import (
    Operation,
    Set,
    edit,
    reconcile_fields,
    resolve,
)
from openhands.sdk.settings import ConversationSettings, OpenHandsAgentSettings

if TYPE_CHECKING:
    from storage.org import Org
    from storage.org_member import OrgMember
    from storage.team import Team

SETTINGS_DOCUMENT_SCHEMA_VERSION = 1

SCHEMA_VERSION_KEY = 'schema_version'
AGENT_SETTINGS_KEY: Final = 'agent_settings'
CONVERSATION_SETTINGS_KEY: Final = 'conversation_settings'


def empty_settings_document() -> dict[str, Any]:
    """A fresh, empty document at the current schema version."""
    return {
        SCHEMA_VERSION_KEY: SETTINGS_DOCUMENT_SCHEMA_VERSION,
        AGENT_SETTINGS_KEY: {},
        CONVERSATION_SETTINGS_KEY: {},
    }


def coerce_settings_document(document: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize a persisted document without dropping unknown top-level keys.

    Blocks are always dictionaries so callers can treat them as sparse scopes.
    A literal ``None`` inside a block is preserved: it means "inherit this
    path" to the legacy transport and is a real value to the canonical
    ``resolve`` API, so the storage layer must not rewrite it.
    """
    if not isinstance(document, Mapping):
        return empty_settings_document()

    normalized = dict(document)
    normalized.setdefault(SCHEMA_VERSION_KEY, SETTINGS_DOCUMENT_SCHEMA_VERSION)
    for key in (AGENT_SETTINGS_KEY, CONVERSATION_SETTINGS_KEY):
        block = normalized.get(key)
        normalized[key] = dict(block) if isinstance(block, Mapping) else {}
    return deepcopy(normalized)


class SettingsBlock:
    """Read and replace one block of an ORM owner's settings document."""

    def __init__(self, key: Literal['agent_settings', 'conversation_settings']) -> None:
        self._key = key

    @overload
    def __get__(
        self, instance: None, owner: type[Org | OrgMember]
    ) -> SettingsBlock: ...

    @overload
    def __get__(
        self, instance: Org | OrgMember, owner: type[Org | OrgMember]
    ) -> dict[str, Any]: ...

    def __get__(
        self, instance: Org | OrgMember | None, owner: type[Org | OrgMember]
    ) -> SettingsBlock | dict[str, Any]:
        if instance is None:
            return self
        document: object = instance.settings
        if not isinstance(document, Mapping):
            return {}
        block = document.get(self._key)
        return deepcopy(dict(block)) if isinstance(block, Mapping) else {}

    def __set__(
        self, instance: Org | OrgMember, value: BaseModel | dict[str, Any] | None
    ) -> None:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode='json')
        document = coerce_settings_document(instance.settings)
        document[self._key] = deepcopy(dict(value)) if value else {}
        instance.settings = document


def save_scope(
    owner: Org | OrgMember | Team,
    operations: Iterable[Operation],
    *parents: dict[str, Any],
    local: dict[str, Any] | None = None,
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Edit the locked owner's document, never its effective view. Caller owns commit."""
    base = coerce_settings_document(owner.settings) if local is None else local
    commands = tuple(operations)
    document = edit(base, commands)
    block = CONVERSATION_SETTINGS_KEY
    submitted = {
        op.path[1] for op in commands if isinstance(op, Set) and op.path[:1] == (block,)
    }
    if submitted:
        inherited = resolve(*(layer.get(block, {}) for layer in parents))
        parent = ConversationSettings.from_persisted(inherited).model_dump(mode='json')
        before = ConversationSettings.from_persisted(
            resolve(inherited, base.get(block, {}))
        ).model_dump(mode='json')
        after = ConversationSettings.from_persisted(
            resolve(inherited, document.get(block, {}))
        ).model_dump(mode='json')
        reconcile_fields(
            document[block], base.get(block, {}), parent, before, after, submitted
        )
    elif any(op.path[:1] == (block,) for op in commands):
        ConversationSettings.from_persisted(
            resolve(*(layer.get(block, {}) for layer in (*parents, document)))
        )
    limit = 'tool_concurrency_limit'
    if any(
        isinstance(op, Set) and op.path == (AGENT_SETTINGS_KEY, limit)
        for op in commands
    ):
        inherited = resolve(*(layer.get(AGENT_SETTINGS_KEY, {}) for layer in parents))
        block = AGENT_SETTINGS_KEY
        if document[block].get('agent_kind', inherited.get('agent_kind')) == 'acp':
            raise ValueError('Agent variant does not support execution limits')
        parent_limit = inherited.get(
            limit, OpenHandsAgentSettings.model_fields[limit].default
        )
        before = {limit: base.get(block, {}).get(limit, parent_limit)}
        reconcile_fields(
            document[block],
            base.get(block, {}),
            {limit: parent_limit},
            before,
            document[block],
            [limit],
        )
    if validate is not None:
        validate(document)
    if document != base or local is not None:
        owner.settings = document
