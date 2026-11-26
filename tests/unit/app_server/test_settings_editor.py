"""The one generic editor drives scalar, list, and reset semantics."""

import pytest
from pydantic import BaseModel, Field, ValidationError

from openhands.app_server.settings.settings_editor import (
    apply_settings_edit,
    effective_settings,
    sparse_member_overlay,
)
from openhands.sdk.settings import ConversationSettings


class _Record(BaseModel):
    limit: int = Field(default=500, ge=1)
    note: str | None = None
    tags: list[str] = Field(default_factory=list)


def test_scalar_set_and_unchanged_parent_value():
    parent = ConversationSettings(max_iterations=150)
    edited = apply_settings_edit(parent, {'max_iterations': 83})
    assert edited.max_iterations == 83
    assert parent.max_iterations == 150


def test_invalid_scalar_fails_whole_record_validation():
    with pytest.raises(ValidationError):
        apply_settings_edit(ConversationSettings(), {'max_iterations': 0})


def test_nullable_reset_and_non_nullable_reset_to_default():
    record = _Record(note='x', limit=10)
    assert apply_settings_edit(record, {'note': None}).note is None
    assert apply_settings_edit(record, {'limit': None}).limit == 500
    conv = apply_settings_edit(
        ConversationSettings(security_analyzer='llm'), {'security_analyzer': None}
    )
    assert conv.security_analyzer is None


def test_list_replace_within_one_scope():
    record = _Record(tags=['a'])
    assert apply_settings_edit(record, {'tags': ['a', 'b']}).tags == ['a', 'b']
    assert apply_settings_edit(record, {'tags': ['b']}).tags == ['b']
    assert apply_settings_edit(record, {'tags': []}).tags == []


def test_changed_parent_flows_to_inheriting_member_only():
    parent_v1 = ConversationSettings(max_iterations=100)
    member = effective_settings(parent_v1, {'max_iterations': 83})
    inheriting = effective_settings(parent_v1, {})
    parent_v2 = apply_settings_edit(parent_v1, {'max_iterations': 150})
    assert effective_settings(parent_v2, {'max_iterations': 83}).max_iterations == 83
    assert member.max_iterations == 83
    assert inheriting.max_iterations == 100
    assert effective_settings(parent_v2, {}).max_iterations == 150


def test_unchanged_save_pins_nothing_and_reset_restores_inheritance():
    parent = ConversationSettings(max_iterations=150, security_analyzer='llm')
    effective = effective_settings(parent, {})
    assert sparse_member_overlay(parent, {}, effective) == {}
    pinned = effective_settings(parent, {'max_iterations': 83})
    overlay = sparse_member_overlay(parent, {}, pinned)
    assert overlay == {'max_iterations': 83}
    reset = apply_settings_edit(pinned, {'max_iterations': 150})
    assert sparse_member_overlay(parent, overlay, reset) == {}


def test_preexisting_pin_survives_parent_catch_up_and_redivergence():
    parent = ConversationSettings(max_iterations=100)
    pinned = effective_settings(parent, {'max_iterations': 83})
    overlay = sparse_member_overlay(parent, {}, pinned)
    assert overlay == {'max_iterations': 83}
    caught_up = apply_settings_edit(parent, {'max_iterations': 83})
    # An unrelated, unchanged save must not erase the member's own pin
    # just because the parent now happens to hold the same value.
    assert sparse_member_overlay(caught_up, overlay, pinned) == overlay
    moved = apply_settings_edit(caught_up, {'max_iterations': 150})
    still = sparse_member_overlay(moved, overlay, pinned)
    assert still == {'max_iterations': 83}
    assert effective_settings(moved, still).max_iterations == 83


def test_legacy_full_overlay_and_null_pin_are_preserved_on_unchanged_save():
    parent = ConversationSettings(max_iterations=150, security_analyzer='llm')
    legacy = {'max_iterations': 150, 'security_analyzer': None}
    submitted = effective_settings(parent, legacy)
    assert sparse_member_overlay(parent, legacy, submitted) == legacy
    assert effective_settings(parent, legacy).security_analyzer is None


def test_sparse_overlay_keeps_explicit_nullable_divergence():
    parent = ConversationSettings(security_analyzer='llm')
    submitted = apply_settings_edit(parent, {'security_analyzer': None})
    overlay = sparse_member_overlay(parent, {}, submitted)
    assert overlay == {'security_analyzer': None}
    assert effective_settings(parent, overlay).security_analyzer is None


def test_nested_member_change_never_pins_an_inherited_sibling():
    class Preferences(BaseModel):
        budget: _Record

    parent = Preferences(budget=_Record(limit=40, note='shared'))
    updated = apply_settings_edit(parent, {'budget': {'limit': 25}})
    owned = sparse_member_overlay(parent, {}, updated)
    assert owned == {'budget': {'limit': 25}}
    moved = apply_settings_edit(parent, {'budget': {'note': 'new'}})
    assert effective_settings(moved, owned).budget == _Record(limit=25, note='new')


def test_nested_null_resets_non_nullable_field_and_map_can_remove_keys():
    class Preferences(BaseModel):
        nested: _Record
        mapping: dict[str, int]

    parent = Preferences(nested=_Record(limit=40), mapping={'a': 1, 'b': 2})
    updated = apply_settings_edit(parent, {'nested': {'limit': None}})
    assert updated.nested.limit == 500
    updated.mapping.pop('b')
    overlay = sparse_member_overlay(parent, {}, updated)
    assert overlay == {'nested': {'limit': 500}, 'mapping': {'a': 1}}
    assert effective_settings(parent, overlay) == updated


def test_list_operations_preserve_order_and_remove_only_matching_items():
    from openhands.app_server.settings.settings_editor import (
        SettingsEdit,
        apply_operation,
    )

    source = {'tags': ['a', 'b', 'b']}
    extended = apply_operation(
        source, SettingsEdit(path=['tags'], op='extend', value=['c'])
    )
    assert apply_operation(
        extended, SettingsEdit(path=['tags'], op='remove', value=['b'])
    ) == {'tags': ['a', 'c']}
    assert source == {'tags': ['a', 'b', 'b']}


def test_agent_map_removal_survives_sdk_merge_without_pinning_siblings():
    from openhands.app_server.settings.settings_editor import retain_changed_leaves
    from openhands.sdk.settings import OpenHandsAgentSettings, apply_agent_settings_diff

    parent = OpenHandsAgentSettings(
        llm={'model': 'gpt-5.6', 'litellm_extra_body': {'keep': 1, 'delete_me': 2}}
    )
    changed = apply_agent_settings_diff(
        parent, {'llm': {'litellm_extra_body': {'delete_me': None}}}
    )
    owned = retain_changed_leaves(
        {}, parent.model_dump(mode='json'), changed.model_dump(mode='json'), changed
    )
    assert owned == {'llm': {'litellm_extra_body': {'delete_me': None}}}
    moved = apply_agent_settings_diff(
        parent, {'llm': {'litellm_extra_body': {'keep': 3, 'added': 4}}}
    )
    assert apply_agent_settings_diff(moved, owned).llm.litellm_extra_body == {
        'keep': 3,
        'added': 4,
    }
