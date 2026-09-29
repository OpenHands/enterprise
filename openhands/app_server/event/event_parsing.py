from __future__ import annotations

import copy
import json
import logging
from typing import Any, ClassVar

from pydantic import ConfigDict, ValidationError, model_serializer

from openhands.sdk import Action, Event, Observation

_logger = logging.getLogger(__name__)
_VALID_EVENT_SOURCES = frozenset({'agent', 'user', 'environment', 'hook'})
_UNKNOWN_EVENT_KIND = 'UnknownEvent'


class UnparsedAction(Action):
    """Fallback for action payloads from SDK/client tools unknown to this server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)

    original_kind: str
    raw_payload: dict[str, Any]

    @model_serializer(mode='plain')
    def _serialize_raw_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw_payload)


class UnparsedObservation(Observation):
    """Fallback for observation payloads unknown to this server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)

    original_kind: str
    raw_payload: dict[str, Any]

    @model_serializer(mode='plain')
    def _serialize_raw_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw_payload)


class UnparsedEvent(Event):
    """Fallback for event payloads unknown to this server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra='forbid', frozen=True)

    original_kind: str
    raw_payload: dict[str, Any]

    @model_serializer(mode='plain')
    def _serialize_raw_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw_payload)


def parse_event_payload(payload: Any) -> Event:
    if isinstance(payload, Event):
        return payload
    if not isinstance(payload, dict):
        return payload

    normalized = _normalize_event_payload(copy.deepcopy(payload))
    try:
        return Event.model_validate(normalized)
    except (TypeError, ValueError, ValidationError) as exc:
        return _fallback_unparsed_event(payload, exc)


def parse_event_json(json_data: str | bytes) -> Event:
    return parse_event_payload(json.loads(json_data))


def _normalize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_kind = payload.get('kind')
    if isinstance(event_kind, str) and not _is_known_kind(Event, event_kind):
        return _unparsed_event_payload(payload, event_kind)

    if event_kind == 'ActionEvent':
        _wrap_unknown_nested_schema(payload, 'action', Action, 'UnparsedAction')
    elif event_kind == 'ObservationEvent':
        _wrap_unknown_nested_schema(
            payload, 'observation', Observation, 'UnparsedObservation'
        )
    return payload


def _wrap_unknown_nested_schema(
    event_payload: dict[str, Any],
    field_name: str,
    base_cls: type[Action] | type[Observation],
    fallback_kind: str,
) -> None:
    nested = event_payload.get(field_name)
    if not isinstance(nested, dict):
        return

    nested_kind = nested.get('kind')
    if not isinstance(nested_kind, str) or _is_known_kind(base_cls, nested_kind):
        return

    event_payload[field_name] = {
        'kind': fallback_kind,
        'original_kind': nested_kind,
        'raw_payload': nested,
    }


def _fallback_unparsed_event(payload: dict[str, Any], exc: Exception) -> Event:
    original_kind = payload.get('kind')
    if not isinstance(original_kind, str):
        original_kind = _UNKNOWN_EVENT_KIND
    _logger.warning(
        'event_parsing:falling_back_to_unparsed_event',
        extra={
            'event_kind': original_kind,
            'error': str(exc),
        },
    )
    return Event.model_validate(_unparsed_event_payload(payload, original_kind))


def _unparsed_event_payload(
    payload: dict[str, Any], original_kind: str
) -> dict[str, Any]:
    source = payload.get('source')
    result: dict[str, Any] = {
        'kind': 'UnparsedEvent',
        'source': source if source in _VALID_EVENT_SOURCES else 'environment',
        'original_kind': original_kind,
        'raw_payload': payload,
    }
    for field_name in ('id', 'timestamp', 'parent_id'):
        if field_name in payload and payload[field_name] is not None:
            result[field_name] = payload[field_name]
    return result


def _is_known_kind(
    base_cls: type[Event] | type[Action] | type[Observation], kind: str
) -> bool:
    try:
        base_cls.resolve_kind(kind)
    except ValueError:
        return False
    return True
