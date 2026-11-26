"""One generic editor for persisted Pydantic settings records.

Every scope (organization defaults, member overrides, personal file
settings) persists the same sparse wire format: a JSON object whose keys
are field names of the persisted model. This module is the only place
that interprets that format:

- a present key sets the field (lists replace wholesale, so extending a
  list and removing an item from it are the same operation);
- a present ``None`` on a nullable field resets the field to ``None``;
- an absent key leaves the field untouched (inheritance);
- the merged record is always validated as a whole model, never as
  untyped JSON.

A member's stored record is the *sparse overlay* over its parent: only
fields the member explicitly set, pruned whenever the parent's value
catches up, so changing the parent changes every inheriting member and
an unchanged save pins nothing.
"""

from __future__ import annotations

from types import NoneType
from typing import Any, get_args

from pydantic import BaseModel


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode='json')


def _is_nullable(model_type: type[BaseModel], key: str) -> bool:
    field = model_type.model_fields.get(key)
    if field is None:
        return False
    return NoneType in get_args(field.annotation) or field.default is None


def apply_settings_edit[M: BaseModel](base: M, edit: dict[str, Any]) -> M:
    """Validate ``edit`` over ``base`` and return the complete record.

    ``None`` resets a nullable field to ``None``; for a non-nullable
    field it removes the key so the field falls back to its model
    default, matching the long-standing diff wire format. Lists in
    ``edit`` replace the stored list wholesale. The merged record is
    validated as a whole, so an invalid value fails the save.
    """
    merged = _dump(base)
    for key, value in edit.items():
        if value is None and not _is_nullable(type(base), key):
            merged.pop(key, None)
        else:
            merged[key] = value
    return type(base).model_validate(merged)


def sparse_member_overlay[M: BaseModel](
    parent: M,
    previous_overlay: dict[str, Any],
    submitted: M,
) -> dict[str, Any]:
    """Sparse member record after a save of the effective ``submitted``.

    Ownership is per-field and sticky: a field this save actually
    changed (against the effective record the member started from)
    is pinned to the submitted value, or dropped when the submission
    returns it to the parent's value (explicit reset / inheritance
    restored). A field this save did not change keeps whatever the
    member already owned — including a legacy pin the parent has
    since caught up with, and an explicit ``None`` — so an unrelated
    save can never silently re-point a member at a future parent
    change, and a member who never diverged pins nothing.
    """
    parent_dump = _dump(parent)
    submitted_dump = _dump(submitted)
    previous_effective = _dump(effective_settings(parent, previous_overlay))
    overlay: dict[str, Any] = {}
    for key in sorted(set(previous_overlay) | set(submitted_dump)):
        if key not in submitted_dump:
            continue
        if submitted_dump[key] == previous_effective.get(key):
            if key in previous_overlay:
                overlay[key] = previous_overlay[key]
        elif submitted_dump[key] != parent_dump.get(key):
            overlay[key] = submitted_dump[key]
    return overlay


def effective_settings[M: BaseModel](parent: M, overlay: dict[str, Any]) -> M:
    """Resolve a member's effective record from parent + sparse overlay."""
    return apply_settings_edit(parent, overlay)
