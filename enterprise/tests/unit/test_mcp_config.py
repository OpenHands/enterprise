from storage.mcp_config import (
    coerce_persisted_mcp_config,
    serialize_mcp_config,
)


def test_serialize_mcp_config_migrates_legacy_wrapper_and_scalar_auth():
    serialized = serialize_mcp_config(
        {
            'mcpServers': {
                'shttp': {
                    'url': 'https://example.com/mcp',
                    'timeout': 60,
                    'auth': 'legacy-token',
                }
            }
        }
    )

    assert serialized == {
        'shttp': {
            'url': 'https://example.com/mcp',
            'timeout': 60.0,
            'auth': {'strategy': 'bearer', 'value': 'legacy-token'},
        }
    }


def test_coerce_persisted_mcp_config_accepts_already_flat_server_map():
    migrated = coerce_persisted_mcp_config(
        {
            'local': {
                'command': 'python',
                'args': ['-m', 'example_server'],
                'env': {'TOKEN': 'current-token'},
            }
        }
    )

    assert migrated['local'].command == 'python'
    assert migrated['local'].args == ['-m', 'example_server']
    assert migrated['local'].env is not None
    assert migrated['local'].env['TOKEN'].get_secret_value() == 'current-token'
