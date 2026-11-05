import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / 'migrations'
    / 'versions'
    / '137_encrypt_member_mcp_config.py'
)
spec = spec_from_file_location('migration_137', MIGRATION_PATH)
assert spec is not None and spec.loader is not None
migration_137 = module_from_spec(spec)
spec.loader.exec_module(migration_137)


def _json_object(value):
    return json.loads(value) if isinstance(value, str) else value


def test_bearer_token_skips_malformed_authorization_values():
    assert (
        migration_137._mcp_bearer_token(
            {
                'headers': {
                    'AUTHORIZATION': None,
                    'Authorization': 'Bearer real-key',
                }
            }
        )
        == 'real-key'
    )


def test_recovery_preserves_non_bearer_auth_shape():
    recovered = migration_137._recover_redacted_mcp_config(
        {
            'server': {
                'url': 'https://mcp.example.com',
                'auth': {'strategy': 'bearer', 'value': '**********'},
            }
        },
        {
            'server': {
                'url': 'https://mcp.example.com',
                'auth': {
                    'strategy': 'api_key',
                    'value': 'real-key',
                    'header_name': 'X-API-Key',
                },
            }
        },
    )

    assert recovered['server']['auth'] == {
        'strategy': 'api_key',
        'value': 'real-key',
        'header_name': 'X-API-Key',
    }


def test_recovery_preserves_typed_bearer_from_scalar_redaction():
    recovered = migration_137._recover_redacted_mcp_config(
        {
            'server': {
                'url': 'https://mcp.example.com',
                'auth': '**********',
            }
        },
        {
            'server': {
                'url': 'https://mcp.example.com',
                'auth': {'strategy': 'bearer', 'value': 'real-key'},
            }
        },
    )

    assert recovered['server']['auth'] == {
        'strategy': 'bearer',
        'value': 'real-key',
    }


def test_recovery_preserves_typed_basic_from_scalar_redaction():
    recovered = migration_137._recover_redacted_mcp_config(
        {
            'server': {
                'url': 'https://mcp.example.com',
                'auth': '**********',
            }
        },
        {
            'server': {
                'url': 'https://mcp.example.com',
                'auth': {
                    'strategy': 'basic',
                    'username': 'user',
                    'password': 'real-key',
                },
            }
        },
    )

    assert recovered['server']['auth'] == {
        'strategy': 'basic',
        'username': 'user',
        'password': 'real-key',
    }
