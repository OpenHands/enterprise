from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

import stripe

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


def test_upgrade_reads_stripe_object_metadata_and_preserves_canonical_customer(
    monkeypatch,
):
    customers = stripe.ListObject.construct_from(
        {
            'object': 'list',
            'data': [
                {
                    'object': 'customer',
                    'id': 'cus_keep',
                    'metadata': {'user_id': 'user-1'},
                },
                {
                    'object': 'customer',
                    'id': 'cus_duplicate',
                    'metadata': {'user_id': 'user-1'},
                },
            ],
            'has_more': False,
        },
        'sk_test_dummy',
    )
    assert not isinstance(customers.data[0].metadata, dict)
    deleted = []
    writes = []

    def execute(statement, params=None):
        if str(statement).startswith('SELECT '):
            return [('user-1', 'cus_keep')]
        writes.append(params)
        return None

    monkeypatch.setenv('STRIPE_API_KEY', 'sk_test_dummy')
    monkeypatch.setattr(stripe, 'api_key', None)
    monkeypatch.setattr(stripe.Customer, 'list', lambda: customers)
    monkeypatch.setattr(stripe.Customer, 'delete', deleted.append)
    monkeypatch.setattr(
        migration_019,
        'op',
        SimpleNamespace(get_bind=lambda: SimpleNamespace(execute=execute)),
    )

    migration_019.upgrade()

    assert deleted == ['cus_duplicate']
    assert writes == [
        {
            'revision': '019',
            'data': '{"user_id": "user-1", "customer_id": "cus_duplicate"}',
        }
    ]
