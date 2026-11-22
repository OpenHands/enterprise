"""The versioned JSON envelope shared by organization, team and member rows."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

SETTINGS_DOCUMENT_SCHEMA_VERSION = 1

SCHEMA_VERSION_KEY = 'schema_version'
AGENT_SETTINGS_KEY = 'agent_settings'
CONVERSATION_SETTINGS_KEY = 'conversation_settings'


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

    normalized = deepcopy(dict(document))
    normalized.setdefault(SCHEMA_VERSION_KEY, SETTINGS_DOCUMENT_SCHEMA_VERSION)
    for key in (AGENT_SETTINGS_KEY, CONVERSATION_SETTINGS_KEY):
        block = normalized.get(key)
        normalized[key] = deepcopy(dict(block)) if isinstance(block, Mapping) else {}
    return normalized


def agent_settings_from_document(document: Mapping[str, Any] | None) -> dict[str, Any]:
    """The document's agent block, as a detached dictionary."""
    if not isinstance(document, Mapping):
        return {}
    block = document.get(AGENT_SETTINGS_KEY)
    return deepcopy(dict(block)) if isinstance(block, Mapping) else {}


def conversation_settings_from_document(
    document: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """The document's conversation block, as a detached dictionary."""
    if not isinstance(document, Mapping):
        return {}
    block = document.get(CONVERSATION_SETTINGS_KEY)
    return deepcopy(dict(block)) if isinstance(block, Mapping) else {}


def with_agent_settings(
    document: Mapping[str, Any] | None, value: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Replace only the agent block, preserving the rest of the document."""
    normalized = coerce_settings_document(document)
    normalized[AGENT_SETTINGS_KEY] = deepcopy(dict(value)) if value else {}
    return normalized


def with_conversation_settings(
    document: Mapping[str, Any] | None, value: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Replace only the conversation block, preserving the rest."""
    normalized = coerce_settings_document(document)
    normalized[CONVERSATION_SETTINGS_KEY] = deepcopy(dict(value)) if value else {}
    return normalized
