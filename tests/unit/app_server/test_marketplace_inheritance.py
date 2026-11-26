"""Real inherited-list semantics: marketplaces compose, never replace.

Parent [A, B]; a member adds C; the parent then adds D. The member's
effective list is [A, B, D, C] (instance/org additions flow through,
the member's own addition survives), and removing a member entry never
removes an inherited one. This is the product path the settings router
and conversation start both use (``compose_marketplaces``).
"""

from openhands.app_server.settings.marketplace_composition import (
    compose_marketplaces,
    duplicate_marketplace_names,
)


def _mp(name: str) -> dict:
    return {'name': name, 'source': f'github:acme/{name}'}


def test_parent_addition_flows_through_and_member_addition_survives():
    composed = compose_marketplaces([_mp('a'), _mp('b')], [], [_mp('c')])
    assert [m.name for m in composed.all] == ['a', 'b', 'c']

    recomposed = compose_marketplaces([_mp('a'), _mp('b')], [_mp('d')], [_mp('c')])
    assert [m.name for m in recomposed.all] == ['a', 'b', 'd', 'c']
    assert [m.name for m in recomposed.inherited] == ['a', 'b', 'd']
    assert [m.name for m in recomposed.personal] == ['c']


def test_member_cannot_shadow_or_remove_inherited_entry():
    composed = compose_marketplaces([_mp('a'), _mp('b')], [], [_mp('b'), _mp('c')])
    assert [m.name for m in composed.all] == ['a', 'b', 'c']
    assert [m.name for m in composed.personal] == ['c']
    # Removing the member's only own entry leaves the inherited list intact.
    after_removal = compose_marketplaces([_mp('a'), _mp('b')], [], [])
    assert [m.name for m in after_removal.all] == ['a', 'b']


def test_org_overrides_instance_same_name_and_write_rejects_duplicates():
    composed = compose_marketplaces(
        [{'name': 'a', 'source': 'github:acme/a', 'auto_load': False}],
        [{'name': 'a', 'source': 'github:acme/a', 'auto_load': True}],
        [],
    )
    assert len(composed.inherited) == 1
    assert composed.inherited[0].auto_load is True
    assert duplicate_marketplace_names([_mp('x'), _mp('x')]) == ['x']
    assert duplicate_marketplace_names([_mp('a')], reserved_names=['a']) == ['a']
