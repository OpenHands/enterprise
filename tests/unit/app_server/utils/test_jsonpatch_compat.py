"""Tests for jsonpatch_compat utilities."""

from openhands.app_server.utils.jsonpatch_compat import deep_merge


class TestDeepMerge:
    """Tests for the base deep_merge function."""

    def test_basic_merge(self):
        base = {'a': 1, 'b': 2}
        updates = {'b': 3, 'c': 4}
        result = deep_merge(base, updates)
        assert result == {'a': 1, 'b': 3, 'c': 4}

    def test_nested_merge(self):
        base = {'outer': {'inner1': 1, 'inner2': 2}}
        updates = {'outer': {'inner2': 3, 'inner3': 4}}
        result = deep_merge(base, updates)
        assert result == {'outer': {'inner1': 1, 'inner2': 3, 'inner3': 4}}

    def test_none_removes_key(self):
        base = {'a': 1, 'b': 2}
        updates = {'b': None}
        result = deep_merge(base, updates)
        assert result == {'a': 1}

    def test_does_not_mutate_base(self):
        base = {'a': 1}
        updates = {'b': 2}
        deep_merge(base, updates)
        assert base == {'a': 1}
