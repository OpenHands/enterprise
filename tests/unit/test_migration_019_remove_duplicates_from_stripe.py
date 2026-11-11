"""Tests for migration 019 (stripe customer dedupe).

stripe>=12 returns ``metadata`` as a :class:`stripe.StripeObject`, not a dict.
The migration must not call dict methods on it, or a fresh database with
``STRIPE_API_KEY`` set fails to come up (see #1334).

These tests stub ``stripe.Customer`` with real ``StripeObject`` instances
constructed via ``construct_from`` so the metadata shape matches production,
without contacting Stripe.
"""

from __future__ import annotations

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '019_remove_duplicates_from_stripe.py'
)
spec = spec_from_file_location('migration_019', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_019 = module_from_spec(spec)
spec.loader.exec_module(migration_019)


def _make_customer(customer_id: str, user_id: str | None):
    """Build a stripe.Customer-like object whose metadata is a real StripeObject."""
    import stripe

    return stripe.Customer.construct_from(
        {'id': customer_id, 'metadata': {'user_id': user_id} if user_id else {}},
        key='sk_test',
    )


class _ListObject:
    """A minimal stripe.ListObject stand-in: supports len() and ``.data``."""

    def __init__(self, items):
        self.data = list(items)

    def __len__(self):
        return len(self.data)

    def auto_paging_iter(self):
        yield from self.data


def _stub_stripe(monkeypatch, customers, payment_methods_by_customer=None):
    payment_methods_by_customer = payment_methods_by_customer or {}

    fake_stripe = SimpleNamespace(
        api_key=None,
        Customer=SimpleNamespace(
            list=lambda: _ListObject(customers),
            list_payment_methods=lambda cid: _ListObject(
                list(payment_methods_by_customer.get(cid, []))
            ),
            delete=MagicMock(),
        ),
    )
    monkeypatch.setattr(migration_019, 'stripe', fake_stripe)
    return fake_stripe


class _Bind:
    """Captures INSERTs into ``script_results`` without needing the DB here."""

    dialect = SimpleNamespace(name='postgresql')

    def __init__(self):
        self.inserts: list[tuple[str, dict]] = []

    def execute(self, statement, params=None):
        compiled = dict(statement.compile().params) if params is None else params
        sql = ' '.join(str(statement).split())
        if sql.upper().startswith('INSERT'):
            self.inserts.append((sql, compiled))
        return []


def _run(monkeypatch, customers, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    bind = _Bind()
    monkeypatch.setattr(migration_019, 'op', SimpleNamespace(get_bind=lambda: bind))
    _stub_stripe(monkeypatch, customers)
    migration_019.upgrade()
    return bind


# ── unit: metadata is a StripeObject, not a dict ─────────────────────────────


def test_upgrade_skips_when_stripe_api_key_missing(monkeypatch):
    monkeypatch.delenv('STRIPE_API_KEY', raising=False)
    bind = _Bind()
    monkeypatch.setattr(migration_019, 'op', SimpleNamespace(get_bind=lambda: bind))
    # No stripe stub needed: the migration returns before listing customers.
    migration_019.upgrade()
    assert bind.inserts == []


def test_upgrade_reads_user_id_from_stripe_object_metadata(monkeypatch):
    """The regression: a StripeObject has no ``.get``; this must not raise."""
    customers = [
        _make_customer('cus_a', 'user-1'),
        _make_customer('cus_b', None),  # no user_id -> skipped
    ]
    bind = _run(monkeypatch, customers, STRIPE_API_KEY='sk_test')

    # One user with one customer -> nothing to delete.
    assert bind.inserts == []


def test_upgrade_deletes_duplicate_customer_ids(monkeypatch):
    customers = [
        _make_customer('cus_a', 'user-1'),
        _make_customer('cus_b', 'user-1'),
        _make_customer('cus_c', 'user-1'),
    ]
    bind = _Bind()
    monkeypatch.setenv('STRIPE_API_KEY', 'sk_test')
    monkeypatch.setattr(migration_019, 'op', SimpleNamespace(get_bind=lambda: bind))
    fake_stripe = _stub_stripe(
        monkeypatch,
        customers,
        payment_methods_by_customer={
            'cus_a': ['pm_1'],
            'cus_b': [],
            'cus_c': ['pm_2', 'pm_3'],
        },
    )
    migration_019.upgrade()

    # 3 customers for user-1, no canonical id in stripe_customers: keep the
    # one with the most payment methods (cus_c, 2), delete the other two.
    deleted = sorted(
        json.loads(params['data'])['customer_id'] for _, params in bind.inserts
    )
    assert deleted == ['cus_a', 'cus_b']
    assert fake_stripe.Customer.delete.call_count == 2
