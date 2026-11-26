"""The versioned JSON envelope shared by organization, team and member rows."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Final, Literal, overload

from pydantic import BaseModel, ValidationError

from openhands.app_server.settings.meta_profiles import ORG_OWNED_ROUTER_SETTINGS_KEYS
from openhands.app_server.settings.scoped import Inherit, Operation, Set, edit
from openhands.sdk.settings import (
    ACPAgentSettings,
    ConversationSettings,
    OpenHandsAgentSettings,
)
from openhands.sdk.settings.model import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    AgentSettingsBase,
    _migrate_agent_settings_payload,
)
from openhands.sdk.tool import Tool
from openhands.sdk.tool.defaults import (
    RETIRED_TOOL_SWITCHES,
    fold_retired_tool_switches,
)
from openhands.sdk.utils.cipher import Cipher
from openhands.sdk.utils.model_overlay import overlay_model, reconcile_model
from openhands.sdk.utils.pydantic_secrets import is_redacted_secret
from storage.encrypt_utils import get_jwt_service
from storage.org_default_settings import create_base_org_agent_settings

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
    A literal ``None`` inside a block is an explicit value, not inheritance.
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


_AGENT = AGENT_SETTINGS_KEY
_CONVERSATION = CONVERSATION_SETTINGS_KEY
_PRIVATE_AGENT_PATHS = {
    ('llm', 'api_key'),
    ('mcp_config',),
    *((name,) for name in ORG_OWNED_ROUTER_SETTINGS_KEYS),
}
_AGENT_TYPES: dict[str, type[AgentSettingsBase]] = {
    'acp': ACPAgentSettings,
    'openhands': OpenHandsAgentSettings,
}


@lru_cache(maxsize=4)
def _cipher_for_keys(current: str, previous: tuple[str, ...]) -> Cipher:
    return Cipher(current, previous)


def settings_cipher() -> Cipher:
    jwt = get_jwt_service()
    return _cipher_for_keys(
        jwt.get_key(jwt.default_key_id).key.get_secret_value(),
        tuple(
            jwt.get_key(key).key.get_secret_value()
            for key in jwt.key_ids
            if key != jwt.default_key_id
        ),
    )


def _agent_values(values: dict[str, Any]) -> dict[str, Any]:
    """Adapt old rows while leaving their sparse ownership intact."""
    values = dict(values)
    for path in _PRIVATE_AGENT_PATHS:
        if len(path) == 1:
            values.pop(path[0], None)
    if isinstance(values.get('llm'), dict):
        values['llm'] = {
            k: v
            for k, v in values['llm'].items()
            if k not in {'api_key', 'is_subscription'}
        }
        if not values['llm']:
            del values['llm']
    if 'schema_version' not in values:
        if values.get('tools') == []:
            values.pop('tools')
        values = fold_retired_tool_switches(values, sparse=True)
    migrated = _migrate_agent_settings_payload(values, persisted=True)
    if 'agent_kind' not in values:
        migrated.pop('agent_kind', None)
    elif migrated.get('agent_kind') == 'llm':
        migrated['agent_kind'] = 'openhands'
    for key in RETIRED_TOOL_SWITCHES:
        migrated.pop(key, None)
    if isinstance(tools := migrated.get('tools'), list):
        migrated['tools'] = [
            t.model_dump(mode='json') if isinstance(t, Tool) else t for t in tools
        ]
    migrated.pop('schema_version')
    return migrated


def _agent_base(parent: AgentSettingsBase, kind: str) -> AgentSettingsBase:
    if kind == parent.agent_kind:
        return parent
    if not isinstance(kind, str) or kind not in _AGENT_TYPES:
        raise ValueError('Invalid agent_kind')
    # Match the SDK's shared fields on a variant switch. MCP is detached.
    shared: dict[str, Any] = {}
    if getattr(getattr(parent, 'agent_context', None), 'load_memory', False):
        shared['agent_context'] = {'load_memory': True}
    if key := getattr(getattr(parent, 'verification', None), 'critic_api_key', None):
        shared['verification'] = {'critic_api_key': key}
    return _AGENT_TYPES[kind].model_validate(shared)


def _overlay(
    block: str, parent: BaseModel, values: dict[str, Any], cipher: Cipher
) -> BaseModel:
    if isinstance(parent, AgentSettingsBase) and 'agent_kind' in values:
        parent = _agent_base(parent, values['agent_kind'])
    return overlay_model(parent, values, cipher=cipher)


def resolve_scope(block: str, *layers: dict[str, Any]) -> BaseModel:
    """Resolve SDK and installation defaults through the given owner layers."""
    cipher = settings_cipher()
    model: BaseModel = (
        create_base_org_agent_settings() if block == _AGENT else ConversationSettings()
    )
    for layer in layers:
        values = (layer or {}).get(block, {}) or {}
        if block == _AGENT:
            values = _agent_values(values)
        model = _overlay(block, model, values, cipher)
    return model


def _check_submission(value: Any) -> None:
    if isinstance(value, Mapping):
        for child in value.values():
            _check_submission(child)
    elif isinstance(value, list):
        for child in value:
            _check_submission(child)
    elif isinstance(value, str) and is_redacted_secret(value):
        raise ValueError('A redacted value is not writable')


def _save_block(
    block: str,
    parent: BaseModel,
    owned: dict[str, Any],
    values: dict[str, Any],
    resets: tuple[Inherit, ...],
    cipher: Cipher,
) -> dict[str, Any]:
    local = edit(owned, resets)
    compare_parent = parent
    if isinstance(parent, AgentSettingsBase):
        kind = values.get('agent_kind', local.get('agent_kind', parent.agent_kind))
        compare_parent = _agent_base(parent, kind)
        if kind != local.get('agent_kind', parent.agent_kind):
            local = {
                k: v
                for k, v in local.items()
                if k == 'verification'
                or k == 'agent_context'
                and set(v or {}) <= {'load_memory'}
            }
            local.pop('agent_kind', None)
            # A variant switch removes the outgoing variant, not its parent's values.
            if kind != parent.agent_kind:
                local['agent_kind'] = kind
        elif any(op.path == ('agent_kind',) for op in resets):
            local = {k: v for k, v in local.items() if k in type(parent).model_fields}
    try:
        before = _overlay(block, compare_parent, local, cipher)
    except ValidationError:
        if block != _CONVERSATION:
            raise
        # A parent can invalidate a child's cross-field constraint. Do not
        # require a valid old model to make a targeted repair.
        before = compare_parent
        after = _overlay(block, compare_parent, {**local, **values}, cipher)
        local = {k: v for k, v in local.items() if k not in values}
    else:
        after = _overlay(block, before, values, cipher)
    local = reconcile_model(compare_parent, before, after, local, values, cipher=cipher)
    if isinstance(parent, AgentSettingsBase) and isinstance(after, AgentSettingsBase):
        if any(
            key not in ACPAgentSettings.model_fields
            or key not in OpenHandsAgentSettings.model_fields
            for key in local
        ):
            local['agent_kind'] = after.agent_kind
        elif (
            local != owned
            and local.get('agent_kind') == parent.agent_kind
            and any(
                name not in ACPAgentSettings.model_fields
                or name not in OpenHandsAgentSettings.model_fields
                for name in owned
            )
        ):
            local.pop('agent_kind', None)
    _overlay(block, parent, local, cipher)
    return local


def save_scope(
    owner: Org | OrgMember | Team,
    operations: Iterable[Operation] = (),
    *parents: dict[str, Any],
    submitted: Mapping[str, Any] | None = None,
    inherit: Iterable[tuple[str, ...]] = (),
    validate: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Reconcile only named preferences of a locked owner. Caller owns commit."""
    commands = (*operations, *(Inherit(path) for path in inherit))
    for op in commands:
        if not op.path:
            raise ValueError('A settings operation must name a path')
        if len(op.path) == 1 and isinstance(op, Set):
            raise ValueError('Expected an owner preference path')
        if op.path[0] not in (_AGENT, _CONVERSATION):
            raise ValueError('Unknown settings block')
    values = edit(dict(submitted or {}), (op for op in commands if isinstance(op, Set)))
    resets = tuple(op for op in commands if isinstance(op, Inherit))
    if set(values) - {_AGENT, _CONVERSATION}:
        raise ValueError('Unknown settings block')
    base = coerce_settings_document(owner.settings)
    document = base.copy()
    cipher = settings_cipher()
    for block in (_AGENT, _CONVERSATION):
        patch = values.get(block, {})
        if not isinstance(patch, dict):
            raise ValueError('Expected a settings object')
        block_resets = tuple(
            Inherit(op.path[1:]) for op in resets if op.path[0] == block
        )
        if not patch and not block_resets:
            continue
        if block == _AGENT:
            if (
                'schema_version' in patch
                or any(
                    path[0] in patch for path in _PRIVATE_AGENT_PATHS if len(path) == 1
                )
                or isinstance(patch.get('llm'), Mapping)
                and 'api_key' in patch['llm']
                or any(
                    reset.path
                    and (
                        reset.path == ('schema_version',)
                        or any(
                            reset.path[: len(path)] == path
                            for path in _PRIVATE_AGENT_PATHS
                        )
                    )
                    for reset in block_resets
                )
            ):
                raise ValueError(
                    'Credential, policy or schema metadata is not writable here'
                )
        _check_submission(patch)
        if 'schema_version' in patch or any(
            'schema_version' in op.path for op in block_resets
        ):
            raise ValueError('Schema metadata is not writable here')
        parent = resolve_scope(block, *parents)
        owned = _agent_values(base[block]) if block == _AGENT else base[block]
        local = _save_block(
            block,
            parent,
            owned if not any(not op.path for op in block_resets) else {},
            patch,
            tuple(op for op in block_resets if op.path),
            cipher,
        )
        changed = local != owned or any(
            key in base[block] for key in RETIRED_TOOL_SWITCHES
        )
        if block == _AGENT and changed:
            if local and ('tools' in local or 'schema_version' in base[block]):
                local['schema_version'] = AGENT_SETTINGS_SCHEMA_VERSION
            # Legacy credentials/policy have a separate lifecycle. Do not mutate
            # them as a side effect of preference reconciliation.
            for path in _PRIVATE_AGENT_PATHS:
                if len(path) == 1 and path[0] in base[block]:
                    local[path[0]] = base[block][path[0]]
        if changed:
            document[block] = local
    if validate is not None:
        validate(document)
    if document != base:
        owner.settings = document
