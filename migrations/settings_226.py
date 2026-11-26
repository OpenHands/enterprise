"""The revision 226 legacy settings conversion (no ORM writes)."""

from copy import deepcopy
from typing import Any, TypeVar

from pydantic import BaseModel

from openhands.sdk.settings import (
    AGENT_SETTINGS_SCHEMA_VERSION,
    ACPAgentSettings,
    AgentSettingsBase,
    CondenserSettings,
    ConversationSettings,
    NoOpCondenserSettings,
    OpenHandsAgentSettings,
)
from openhands.sdk.settings.model import _migrate_agent_settings_payload
from openhands.sdk.tool.defaults import (
    RETIRED_TOOL_SWITCHES,
    fold_retired_tool_switches,
)
from openhands.sdk.utils.model_overlay import overlay_model
from storage.org_default_settings import create_base_org_agent_settings


class ConversionError(ValueError):
    """A fixed, non-sensitive preflight diagnostic."""


_VARIANTS: dict[str, type[AgentSettingsBase]] = {
    'openhands': OpenHandsAgentSettings,
    'acp': ACPAgentSettings,
}
_ROUTER_DEFAULTS: dict[str, Any] = {
    'enable_classify_and_switch_llm_tool': False,
    'active_meta_profile': None,
    'meta_profile': None,
    'meta_profile_llms': {},
}


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConversionError('Expected a JSON object')
    return deepcopy(value)


def _placeholder(value: Any) -> bool:
    return value is None or isinstance(value, str) and not value.strip().strip('*')


def _remove_inline_credentials(agent: dict[str, Any]) -> None:
    # Recognize placeholders, but never discard a meaningful value just because
    # some encrypted slot (possibly for a different credential) is populated.
    if agent.pop('mcp_config', None) not in (None, {}):
        raise ConversionError('real inline mcp_config')
    for record, keys in (
        (
            'llm',
            (
                'api_key',
                'aws_access_key_id',
                'aws_secret_access_key',
                'aws_session_token',
            ),
        ),
        ('verification', ('critic_api_key',)),
        ('agent_context', ('secrets',)),
    ):
        block = agent.get(record)
        if not isinstance(block, dict):
            continue
        for key in keys:
            if key not in block:
                continue
            value = block.pop(key)
            if not _placeholder(value) and not (key == 'secrets' and value == {}):
                raise ConversionError(f'real inline {record}.{key}')
        if record == 'llm' and block.get('extra_headers') not in (None, {}):
            raise ConversionError('potential credentials in inline llm.extra_headers')
        if not block:
            agent.pop(record)
    for key, default in _ROUTER_DEFAULTS.items():
        if agent.pop(key, default) != default:
            raise ConversionError(
                'non-default inline router settings require remediation'
            )


def _agent_values(
    raw: Any, parent: AgentSettingsBase, *, member: bool
) -> dict[str, Any]:
    values = _object(raw)
    _remove_inline_credentials(values)
    explicit_kind = 'agent_kind' in values and not (
        member and values['agent_kind'] is None
    )
    kind = values.get('agent_kind') if explicit_kind else parent.agent_kind
    if kind == 'llm':
        kind = 'openhands'
    if not isinstance(kind, str) or kind not in _VARIANTS:
        raise ConversionError('unknown agent variant')
    values['agent_kind'] = kind
    if kind == 'acp' and values.keys() & {'tools', *RETIRED_TOOL_SWITCHES}:
        raise ConversionError('OpenHands tool settings on an ACP row')
    llm = values.get('llm')
    if isinstance(llm, dict):
        # Older writers dumped this computed compatibility value with every LLM.
        if 'is_subscription' in llm and not isinstance(llm['is_subscription'], bool):
            raise ConversionError('invalid legacy subscription marker')
        if llm.pop('is_subscription', False) and llm.get('auth_type') != 'subscription':
            raise ConversionError('subscription marker without subscription auth')
        if not llm:
            values.pop('llm')
    if member and values.get('schema_version') is None:
        if values.get('tools') == []:
            values.pop('tools')
        if kind == 'openhands' and any(key in values for key in RETIRED_TOOL_SWITCHES):
            values = fold_retired_tool_switches(values, sparse=True)
    if (
        member
        and kind == 'openhands'
        and isinstance(parent, OpenHandsAgentSettings)
        and type(values.get('schema_version')) is int
        and values['schema_version'] < 8
        and 'tools' not in values
        and any(key in values for key in RETIRED_TOOL_SWITCHES)
        and parent.tools is not None
    ):
        # Versioned legacy diffs ran the SDK migration on the merged tool list.
        values['tools'] = parent.tools
    condenser = values.get('condenser')
    if (
        isinstance(condenser, dict)
        and 'condenser_kind' not in condenser
        and condenser.keys() - {'enabled'}
        and isinstance(parent, OpenHandsAgentSettings)
    ):
        condenser['condenser_kind'] = parent.condenser.condenser_kind
    values = _migrate_agent_settings_payload(values, persisted=True)
    values.pop('schema_version')
    other = _VARIANTS['acp' if kind == 'openhands' else 'openhands']
    context = values.get('agent_context', {})
    verification = values.get('verification')
    if not explicit_kind and not (
        (values.keys() - other.model_fields.keys())
        or bool(verification)
        or (kind == 'acp' and context is None)
        or (isinstance(context, dict) and bool(context.keys() - {'load_memory'}))
    ):
        values.pop('agent_kind')
    return values


def _agent_parent(
    parent: AgentSettingsBase, values: dict[str, Any]
) -> AgentSettingsBase:
    kind: str = values.get('agent_kind', parent.agent_kind)
    if kind == parent.agent_kind:
        return parent
    shared: dict[str, Any] = {}
    if getattr(getattr(parent, 'agent_context', None), 'load_memory', False):
        shared['agent_context'] = {'load_memory': True}
    return _VARIANTS[kind].model_validate(shared)


def _member_defaults(parent: BaseModel, values: dict[str, Any]) -> dict[str, Any]:
    """Translate legacy merge-patch nulls (reset to model default, not inherit)."""
    result = {}
    for key, value in values.items():
        field = type(parent).model_fields.get(key)
        if field is None or field.exclude:
            raise ConversionError('unknown or non-persistable setting')
        inherited = getattr(parent, key)
        if value is None and inherited is not None:
            if field.is_required():
                raise ConversionError('legacy reset of a required setting')
            value = field.get_default(call_default_factory=True)
        elif value is None and field.is_required():
            raise ConversionError('legacy reset of a required setting')
        elif isinstance(value, dict) and isinstance(inherited, BaseModel):
            if (
                isinstance(inherited, CondenserSettings)
                and value.get('condenser_kind', inherited.condenser_kind)
                != inherited.condenser_kind
            ):
                # A legacy merge would retain the old branch's fields; the
                # discriminator selected the new branch and ignored those fields.
                inherited = (
                    NoOpCondenserSettings()
                    if value['condenser_kind'] == 'no_op'
                    else OpenHandsAgentSettings().condenser
                )
            value = _member_defaults(inherited, value)
        elif isinstance(value, dict) and isinstance(inherited, dict) and inherited:
            # Legacy object patches merged maps. The new schema owns the whole
            # map, so a partial override cannot be preserved without promoting
            # some inherited entries to pins.
            if not inherited.keys() <= value.keys() or any(
                isinstance(inherited[k], dict) and isinstance(value[k], dict)
                for k in inherited
            ):
                raise ConversionError('partial legacy map needs explicit ownership')
        result[key] = value
    return result


def _owned_shape(model: BaseModel, values: dict[str, Any]) -> dict[str, Any]:
    """Select only legacy-owned leaves, rejecting keys ignored by model validation."""
    shape: dict[str, Any] = {}
    for key, raw in values.items():
        field = type(model).model_fields.get(key)
        if field is None or field.exclude:
            raise ConversionError('unknown or non-persistable setting')
        value = getattr(model, key)
        if isinstance(raw, dict) and isinstance(value, BaseModel):
            child = _owned_shape(value, raw)
            if child:
                shape[key] = child
        else:
            if (
                isinstance(raw, list)
                and isinstance(value, list)
                and len(raw) == len(value)
            ):
                for item, parsed in zip(raw, value, strict=True):
                    if isinstance(item, dict) and isinstance(parsed, BaseModel):
                        _owned_shape(parsed, item)
            shape[key] = True
    return shape


_ModelT = TypeVar('_ModelT', bound=BaseModel)


def _block(
    parent: _ModelT, values: dict[str, Any], *, member: bool
) -> tuple[_ModelT, dict[str, Any]]:
    if member:
        values = _member_defaults(parent, values)
    # A condenser discriminator selects a new default record before overlay.
    # The baseline SDK's overlay handles string tags; this is a callable tag.
    condenser = values.get('condenser')
    if isinstance(parent, OpenHandsAgentSettings) and isinstance(condenser, dict):
        tag = condenser.get('condenser_kind', parent.condenser.condenser_kind)
        if tag != parent.condenser.condenser_kind:
            if tag not in ('no_op', 'llm_summarizing'):
                raise ConversionError('unknown condenser variant')
            template = (
                NoOpCondenserSettings()
                if tag == 'no_op'
                else OpenHandsAgentSettings().condenser
            )
            parent = parent.model_copy(update={'condenser': template})
    model = overlay_model(parent, values)
    document = model.model_dump(mode='json', include=_owned_shape(model, values))
    return model, document


def convert(
    agent: Any,
    conversation: Any,
    parents: tuple[AgentSettingsBase, BaseModel] | None = None,
) -> tuple[dict[str, Any], tuple[AgentSettingsBase, BaseModel]]:
    """Convert an org or a member without subtracting equal values."""
    agent_parent, conversation_parent = parents or (
        create_base_org_agent_settings(),
        ConversationSettings(),
    )
    values = _agent_values(agent, agent_parent, member=parents is not None)
    agent_parent = _agent_parent(agent_parent, values)
    agent_model, agent_document = _block(
        agent_parent, values, member=parents is not None
    )
    conversation_values = _object(conversation)
    version = conversation_values.pop('schema_version', None)
    if version is not None and (type(version) is not int or version not in (0, 1)):
        raise ConversionError('unsupported conversation settings schema')
    conversation_model, conversation_document = _block(
        conversation_parent, conversation_values, member=parents is not None
    )
    # Mark the canonical block so no legacy transformations are reapplied.
    # Leave an empty agent contribution completely empty.
    if agent_document:
        agent_document['schema_version'] = AGENT_SETTINGS_SCHEMA_VERSION
    return (
        {
            'schema_version': 1,
            'agent_settings': agent_document,
            'conversation_settings': conversation_document,
        },
        (agent_model, conversation_model),
    )
