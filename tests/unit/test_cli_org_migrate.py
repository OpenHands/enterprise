from __future__ import annotations

from argparse import Namespace

from openhands.cli import org_migrate


def test_normalize_types_all():
    assert org_migrate._normalize_types('all') == {
        'secrets',
        'keys',
        'mcp',
        'automations',
    }


def test_normalize_types_single():
    assert org_migrate._normalize_types('secrets') == {'secrets'}


def test_validate_user_selection_all_only():
    args = Namespace(all=True, user_file=None, user_identifiers=[])
    assert org_migrate._validate_user_selection(args) is None


def test_validate_user_selection_requires_input():
    args = Namespace(all=False, user_file=None, user_identifiers=[])
    assert org_migrate._validate_user_selection(args) is not None


def test_validate_user_selection_file_and_identifiers_conflict():
    args = Namespace(all=False, user_file='users.txt', user_identifiers=['user'])
    assert org_migrate._validate_user_selection(args) is not None


def test_dedupe_list_preserves_order():
    values = ['a', 'b', 'a', 'c', 'b']
    assert org_migrate._dedupe_list(values) == ['a', 'b', 'c']
