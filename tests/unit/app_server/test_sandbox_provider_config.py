"""Explicit provider configuration validates operator input without exposing secrets."""

import copy
import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import JsonValue, TypeAdapter, ValidationError

from openhands.app_server.sandbox.sandbox_provider_config import (
    RuntimeAPITemplate,
    SandboxProviderConfig,
)


def runtime_env() -> dict[str, str]:
    return {
        'SANDBOX_PROVIDER': 'runtime_api',
        'SANDBOX_DEFAULT_TEMPLATE': 'python',
        'SANDBOX_TEMPLATES': json.dumps(
            [
                {
                    'id': 'python',
                    'config_name': 'native-python',
                }
            ]
        ),
    }


def test_unset_selector_preserves_legacy_configuration() -> None:
    assert SandboxProviderConfig.from_env({'SANDBOX_TEMPLATES': 'invalid json'}) is None


@pytest.mark.parametrize(
    'provider', ['docker', 'e2b', 'agent_sandbox', 'runtim_api', '']
)
def test_unsupported_provider_is_explicit(provider: str) -> None:
    with pytest.raises(ValueError, match='supports runtime_api'):
        SandboxProviderConfig.from_env({'SANDBOX_PROVIDER': provider})


@pytest.mark.parametrize(
    'raw', ['private-json-secret', '{}', 'null', '1', '"private-json-secret"', '[1]']
)
def test_malformed_catalog_is_safe(raw: str) -> None:
    with pytest.raises(ValueError) as error:
        SandboxProviderConfig.from_env(
            {'SANDBOX_PROVIDER': 'runtime_api', 'SANDBOX_TEMPLATES': raw}
        )
    assert 'private-json-secret' not in str(error.value)
    assert 'SANDBOX_TEMPLATES' in str(error.value)


def test_runtime_api_without_catalog_and_optional_bootstrap_reference() -> None:
    config = SandboxProviderConfig.from_env({'SANDBOX_PROVIDER': 'runtime_api'})
    assert config is not None
    assert config.provider == 'runtime_api'
    assert config.templates == []
    env = {
        'SANDBOX_PROVIDER': 'runtime_api',
        'SANDBOX_DEFAULT_TEMPLATE': 'python',
        'SANDBOX_TEMPLATES': '[{"id":"python","config_name":"native-python"}]',
    }
    config = SandboxProviderConfig.from_env(env)
    assert config is not None
    assert isinstance(config.templates[0], RuntimeAPITemplate)
    assert config.templates[0].init_api_key_env is None
    env['SANDBOX_TEMPLATES'] = (
        '[{"id":"python","config_name":"native-python","init_api_key_env":"NATIVE_INIT"}]'
    )
    with pytest.raises(ValueError, match='NATIVE_INIT'):
        SandboxProviderConfig.from_env(env)
    env['NATIVE_INIT'] = 'private-bootstrap-secret'
    assert SandboxProviderConfig.from_env(env) is not None


@pytest.mark.parametrize('value', ['', '   '])
def test_runtime_api_explicit_key_reference_must_remain_nonempty(value: str) -> None:
    with pytest.raises(ValueError, match='NATIVE_INIT'):
        SandboxProviderConfig.from_env(
            {
                'SANDBOX_PROVIDER': 'runtime_api',
                'SANDBOX_DEFAULT_TEMPLATE': 'python',
                'SANDBOX_TEMPLATES': json.dumps(
                    [
                        {
                            'id': 'python',
                            'config_name': 'native-python',
                            'init_api_key_env': 'NATIVE_INIT',
                        }
                    ]
                ),
                'NATIVE_INIT': value,
            }
        )


@pytest.mark.parametrize(
    'mutation',
    [
        'empty',
        'duplicate',
        'missing_default',
        'unknown_default',
        'empty_id',
        'missing_config_name',
        'invalid_env_reference',
        'provider_mismatch',
        'unknown_field',
    ],
)
def test_invalid_catalog(mutation: str) -> None:
    env = runtime_env()
    templates = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
        env['SANDBOX_TEMPLATES']
    )
    if mutation == 'empty':
        templates = []
    elif mutation == 'duplicate':
        templates.append(copy.deepcopy(templates[0]))
    elif mutation == 'missing_default':
        del env['SANDBOX_DEFAULT_TEMPLATE']
    elif mutation == 'unknown_default':
        env['SANDBOX_DEFAULT_TEMPLATE'] = 'missing'
    elif mutation == 'empty_id':
        templates[0]['id'] = ''
    elif mutation == 'missing_config_name':
        del templates[0]['config_name']
    elif mutation == 'invalid_env_reference':
        templates[0]['init_api_key_env'] = 'invalid-env-reference'
    elif mutation == 'provider_mismatch':
        templates[0]['provider'] = 'docker'
    else:
        templates[0]['typo'] = 'private-invalid-field-secret'
    env['SANDBOX_TEMPLATES'] = json.dumps(templates)
    with pytest.raises(ValueError) as error:
        SandboxProviderConfig.from_env(env)
    assert 'private-' not in str(error.value)
    assert 'SANDBOX_' in str(error.value)


def test_direct_model_validation_hides_secret_input() -> None:
    with pytest.raises(ValidationError) as error:
        RuntimeAPITemplate.model_validate(
            {
                'id': 'python',
                'config_name': 'native-python',
                'initial_env': {'KEY': 'private-secret'},
                'unknown': 'private-secret',
            }
        )
    assert 'private-secret' not in str(error.value)
    assert 'private-secret' not in repr(error.value)


@pytest.fixture
def config_environment(tmp_path: Path) -> Iterator[None]:
    with patch.dict(os.environ, {'OH_PERSISTENCE_DIR': str(tmp_path)}, clear=True):
        yield


@pytest.mark.parametrize('variable', ['OH_SANDBOX_KIND', 'OH_SANDBOX_SPEC_KIND'])
def test_explicit_provider_rejects_injector_conflicts(
    config_environment: None, variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.config import config_from_env

    monkeypatch.setenv('SANDBOX_PROVIDER', 'runtime_api')
    monkeypatch.setenv(variable, 'unimportable.conflicting.Injector')
    with pytest.raises(ValueError, match=variable):
        config_from_env()


@pytest.mark.parametrize(
    'legacy_runtime, expected',
    [
        ('remote', 'RemoteSandboxServiceInjector'),
        ('local', 'ProcessSandboxServiceInjector'),
        ('process', 'ProcessSandboxServiceInjector'),
        ('docker', 'DockerSandboxServiceInjector'),
    ],
)
def test_no_selector_preserves_legacy_injectors(
    config_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    legacy_runtime: str,
    expected: str,
) -> None:
    from openhands.app_server.config import config_from_env

    monkeypatch.setenv('RUNTIME', legacy_runtime)
    monkeypatch.setenv('SANDBOX_API_KEY', 'runtime-api-key')
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'https://runtime.example')
    assert type(config_from_env().sandbox).__name__ == expected


def test_explicit_runtime_api_overrides_legacy_runtime(
    config_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.config import config_from_env

    monkeypatch.setenv('RUNTIME', 'local')
    monkeypatch.setenv('SANDBOX_PROVIDER', 'runtime_api')
    monkeypatch.setenv('SANDBOX_API_KEY', 'runtime-api-key')
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'https://runtime.example')
    config = config_from_env()
    assert type(config.sandbox).__name__ == 'RemoteSandboxServiceInjector'
    assert type(config.sandbox_spec).__name__ == 'RemoteSandboxSpecServiceInjector'
    monkeypatch.setenv(
        'SANDBOX_TEMPLATES', '[{"id":"python","config_name":"native-python"}]'
    )
    monkeypatch.setenv('SANDBOX_DEFAULT_TEMPLATE', 'python')
    assert (
        type(config_from_env().sandbox_spec).__name__
        == 'ConfiguredSandboxSpecServiceInjector'
    )


def test_configured_injectors_do_not_contaminate_later_legacy_parsing(
    config_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.config import config_from_env

    monkeypatch.setenv('SANDBOX_PROVIDER', 'runtime_api')
    monkeypatch.setenv('SANDBOX_API_KEY', 'api-key')
    monkeypatch.setenv('SANDBOX_REMOTE_RUNTIME_API_URL', 'https://runtime.example')
    monkeypatch.setenv('SANDBOX_DEFAULT_TEMPLATE', 'python')
    monkeypatch.setenv(
        'SANDBOX_TEMPLATES', '[{"id":"python","config_name":"native-python"}]'
    )
    first = config_from_env()
    assert type(first.sandbox_spec).__name__ == 'ConfiguredSandboxSpecServiceInjector'
    monkeypatch.delenv('SANDBOX_PROVIDER')
    monkeypatch.setenv(
        'OH_SANDBOX_KIND',
        'openhands.app_server.sandbox.process_sandbox_service.ProcessSandboxServiceInjector',
    )
    monkeypatch.setenv(
        'OH_SANDBOX_SPEC_KIND',
        'openhands.app_server.sandbox.process_sandbox_spec_service.ProcessSandboxSpecServiceInjector',
    )
    second = config_from_env()
    assert type(second.sandbox).__name__ == 'ProcessSandboxServiceInjector'
    assert type(second.sandbox_spec).__name__ == 'ProcessSandboxSpecServiceInjector'
