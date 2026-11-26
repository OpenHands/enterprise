"""Validate scoped settings edits and reconcile only the fields a member owns."""

from __future__ import annotations

from collections.abc import Mapping
from types import NoneType
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

type SettingsOperation = Literal['set', 'extend', 'remove', 'inherit']


class SettingsEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')

    path: list[str] = Field(min_length=1, max_length=8)
    op: SettingsOperation
    value: Any = None


def apply_operation(document: dict[str, Any], edit: SettingsEdit) -> dict[str, Any]:
    result = document.copy()
    key, *rest = edit.path
    current = result.get(key)
    if rest:
        if not isinstance(current, Mapping):
            if edit.op == 'inherit':
                return result
            raise ValueError('Settings path does not address an object')
        child = apply_operation(dict(current), edit.model_copy(update={'path': rest}))
        if child or edit.op != 'inherit':
            result[key] = child
        else:
            result.pop(key)
    elif edit.op == 'inherit':
        result.pop(key, None)
    elif edit.op == 'set':
        result[key] = edit.value
    else:
        if not isinstance(current, list) or not isinstance(edit.value, list):
            raise ValueError('List edits require an array path and array value')
        result[key] = (
            current + edit.value
            if edit.op == 'extend'
            else [item for item in current if item not in edit.value]
        )
    return result


def _merge(
    base: dict[str, Any], patch: dict[str, Any], model: BaseModel | None
) -> dict[str, Any]:
    result = base.copy()
    for key, value in patch.items():
        previous = result.get(key)
        field = type(model).model_fields.get(key) if model is not None else None
        child = getattr(model, key, None) if field is not None else None
        if (
            value is None
            and field is not None
            and not (NoneType in get_args(field.annotation) or field.default is None)
        ):
            result.pop(key, None)
        elif isinstance(previous, dict) and isinstance(value, dict):
            result[key] = _merge(
                previous, value, child if isinstance(child, BaseModel) else None
            )
        else:
            result[key] = value
    return result


def apply_settings_edit[M: BaseModel](base: M, edit: dict[str, Any]) -> M:
    return type(base).model_validate(_merge(base.model_dump(mode='python'), edit, base))


def _reconcile(
    parent: dict[str, Any],
    owned: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    model: BaseModel | None,
    *,
    merge_maps: bool = False,
) -> dict[str, Any]:
    result = owned.copy()
    if merge_maps:
        result.update(dict.fromkeys(before.keys() - after.keys()))
    for key, value in after.items():
        if key in before and value == before[key]:
            continue
        inherited = parent.get(key)
        child_model = getattr(model, key, None)
        if (
            (isinstance(child_model, BaseModel) or merge_maps)
            and isinstance(value, dict)
            and (inherited is None or isinstance(inherited, dict))
            and isinstance(before.get(key), dict)
            and isinstance(owned.get(key, {}), dict)
        ):
            child = _reconcile(
                inherited or {},
                owned.get(key, {}),
                before[key],
                value,
                child_model if isinstance(child_model, BaseModel) else None,
                merge_maps=merge_maps,
            )
            if child:
                result[key] = child
            else:
                result.pop(key, None)
        elif key in parent and value == inherited:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def retain_changed_leaves(
    owned: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    model: BaseModel,
) -> dict[str, Any]:
    """Reconcile SDK agent merge patches, including map deletion tombstones."""
    return _reconcile({}, owned, before, after, model, merge_maps=True)


def sparse_member_overlay[M: BaseModel](
    parent: M, previous_overlay: dict[str, Any], submitted: M
) -> dict[str, Any]:
    before = effective_settings(parent, previous_overlay)
    return _reconcile(
        parent.model_dump(mode='json'),
        previous_overlay,
        before.model_dump(mode='json'),
        submitted.model_dump(mode='json'),
        parent,
    )


def effective_settings[M: BaseModel](parent: M, overlay: dict[str, Any]) -> M:
    def compose(current: BaseModel, contribution: dict[str, Any]) -> dict[str, Any]:
        result = current.model_dump(mode='python')
        for key, value in contribution.items():
            child = getattr(current, key, None)
            result[key] = (
                compose(child, value)
                if isinstance(child, BaseModel) and isinstance(value, dict)
                else value
            )
        return result

    return type(parent).model_validate(compose(parent, overlay))


def denied(
    policy: Mapping[str, list[str]], path: list[str], op: SettingsOperation
) -> bool:
    key = '.'.join(path)
    return any(
        op in operations
        and (key == rule or key.startswith(rule + '.') or rule.startswith(key + '.'))
        for rule, operations in policy.items()
    )
