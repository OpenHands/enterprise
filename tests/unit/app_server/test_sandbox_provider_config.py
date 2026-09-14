"""Explicit provider configuration validates operator input without exposing secrets."""

import copy
import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import JsonValue, SecretStr, TypeAdapter, ValidationError

from openhands.app_server.sandbox.sandbox_provider_config import (
    DockerLaunchOptions,
    DockerTemplate,
    RuntimeAPITemplate,
    SandboxProviderConfig,
    _docker_environment,
)


def docker_env() -> dict[str, str]:
    return {
        'SANDBOX_PROVIDER': 'docker',
        'SANDBOX_DEFAULT_TEMPLATE': 'python',
        'SANDBOX_TEMPLATES': json.dumps(
            [
                {
                    'id': 'python',
                    'image': 'custom.example/agent:current',
                }
            ]
        ),
    }


def test_unset_selector_preserves_legacy_configuration() -> None:
    assert SandboxProviderConfig.from_env({'SANDBOX_TEMPLATES': 'invalid json'}) is None


@pytest.mark.parametrize('provider', ['e2b', 'agent_sandbox', 'runtim_api', ''])
def test_unsupported_provider_is_explicit(provider: str) -> None:
    with pytest.raises(ValueError, match='supports runtime_api and docker'):
        SandboxProviderConfig.from_env({'SANDBOX_PROVIDER': provider})


@pytest.mark.parametrize(
    'raw', ['private-json-secret', '{}', 'null', '1', '"private-json-secret"', '[1]']
)
def test_malformed_catalog_is_safe(raw: str) -> None:
    with pytest.raises(ValueError) as error:
        SandboxProviderConfig.from_env(
            {'SANDBOX_PROVIDER': 'docker', 'SANDBOX_TEMPLATES': raw}
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
        'missing_image',
        'obsolete_key_ref',
        'provider_mismatch',
        'unknown_field',
    ],
)
def test_invalid_catalog(mutation: str) -> None:
    env = docker_env()
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
    elif mutation == 'missing_image':
        del templates[0]['image']
    elif mutation == 'obsolete_key_ref':
        templates[0]['init_api_key_env'] = 'LOCAL_INIT_KEY'
    elif mutation == 'provider_mismatch':
        templates[0]['provider'] = 'runtime_api'
    else:
        templates[0]['typo'] = 'private-invalid-field-secret'
    env['SANDBOX_TEMPLATES'] = json.dumps(templates)
    with pytest.raises(ValueError) as error:
        SandboxProviderConfig.from_env(env)
    assert 'private-' not in str(error.value)
    assert 'SANDBOX_' in str(error.value)


@pytest.mark.parametrize('value', [None, '', '   ', 'unused-static-secret'])
def test_docker_needs_no_static_key_reference(value: str | None) -> None:
    env = docker_env()
    if value is not None:
        env['LOCAL_INIT_KEY'] = value
    config = SandboxProviderConfig.from_env(env)
    assert config is not None
    assert isinstance(config.templates[0], DockerTemplate)
    assert 'init_api_key_env' not in config.templates[0].model_dump()
    startup = _docker_environment(config.templates[0])
    assert 'OH_SECRET_KEY' not in startup
    assert not any(name.startswith('OH_SESSION_API_KEYS') for name in startup)


@pytest.mark.parametrize(
    'initial_env',
    [
        {'OH_SECRET_KEY': 'private-wrong-key'},
        {'OH_SESSION_API_KEYS_0': 'private-wrong-key'},
        {'OH_DEFERRED_INIT': 'false'},
        {'OH_SESSION_API_KEYS_1': 'private-alternate-key'},
        {'OH_SESSION_API_KEYS_10': 'private-alternate-key'},
        {'OH_SESSION_API_KEYS_': 'private-alternate-key'},
        {'OH_SESSION_API_KEYS': '["private-alternate-key"]'},
    ],
)
def test_docker_rejects_operator_credentials_and_disabled_deferred_init(
    initial_env: dict[str, str],
) -> None:
    env = docker_env()
    template = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
        env['SANDBOX_TEMPLATES']
    )[0]
    template['initial_env'] = dict(initial_env)
    env['SANDBOX_TEMPLATES'] = json.dumps([template])
    with pytest.raises(ValueError) as error:
        SandboxProviderConfig.from_env(env)
    assert 'private-' not in str(error.value)
    assert 'private-' not in repr(error.value)
    assert error.value.__context__ is None or error.value.__suppress_context__


@pytest.mark.parametrize('truthy', ['true', '1', 'TRUE'])
def test_dormant_toggle_accepts_existing_boolean_formats(truthy: str) -> None:
    env = docker_env()
    template = TypeAdapter(list[dict[str, JsonValue]]).validate_json(
        env['SANDBOX_TEMPLATES']
    )[0]
    template['initial_env'] = {
        'OH_DEFERRED_INIT': truthy,
        'PRIVATE': 'private-template-secret',
    }
    env['SANDBOX_TEMPLATES'] = json.dumps([template])
    config = SandboxProviderConfig.from_env(env)
    assert config is not None
    assert isinstance(config.templates[0], DockerTemplate)
    assert 'private-template-secret' not in repr(config)
    assert 'private-template-secret' not in config.model_dump_json()
    assert (
        _docker_environment(config.templates[0])['OH_DEFERRED_INIT'].get_secret_value()
        == 'true'
    )


@pytest.mark.parametrize('persistence_path', [None, '/sandbox/state'])
def test_docker_persistence_uses_managed_mount_and_preserves_override(
    persistence_path: str | None,
) -> None:
    template = DockerTemplate(
        id='python',
        image='image',
        working_dir='/sandbox/project',
        docker=DockerLaunchOptions(workspace_mount_path='/sandbox'),
        initial_env={'OH_PERSISTENCE_DIR': SecretStr(persistence_path)}
        if persistence_path
        else {},
    )
    startup = _docker_environment(template)
    assert startup['OH_PERSISTENCE_DIR'].get_secret_value() == (
        persistence_path or '/sandbox/.openhands'
    )
    assert startup['OH_WORKSPACE_PATH'].get_secret_value() == '/sandbox/project'


@pytest.mark.parametrize(
    'options',
    [
        {'network': 'host'},
        {'network': 'none'},
        {'network': 'container:another'},
        {'privileged': True},
        {'bind_host': 'not-an-ip'},
        {'ports': {}},
        {'ports': {'AGENT_SERVER': 0}},
        {'ports': {'AGENT_SERVER': '8000'}},
        {'ports': {'AGENT_SERVER': 8000, 'VSCODE': 8000}},
        {'ports': {'AGENT_SERVER': 8000, 'UNSUPPORTED': 8100}},
        {'container_url_pattern': 'http://localhost'},
        {'container_url_pattern': 'http://localhost:{unknown}'},
        {'container_url_pattern': 'http://localhost:{port:06d}'},
        {'container_url_pattern': 'http://localhost/{port}'},
        {'container_url_pattern': 'http://user:password@localhost:{port}'},
        {'container_url_pattern': 'ftp://localhost:{port}'},
        {'webhook_url': 'localhost:3000'},
        {'workspace_mount_path': '/'},
        {'workspace_mount_path': 'relative/path'},
        {'mem_limit': '-1g'},
        {'nano_cpus': 0},
        {'user': 'bad user'},
        {'mounts': [{'source': '/host', 'target': '/workspace/project'}]},
        {'mounts': [{'source': 'relative', 'target': '/data'}]},
    ],
)
def test_invalid_docker_options(options: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        DockerLaunchOptions.model_validate(options)


@pytest.mark.parametrize(
    'pattern',
    [
        'https://{container_port}-{resource_id}.sandboxes.example.com',
        'https://sandbox-{resource_id}-{container_port}.example.com/',
    ],
)
def test_public_docker_url_is_an_https_origin_with_resource_and_service(
    pattern: str,
) -> None:
    options = DockerLaunchOptions(public_url_pattern=pattern)
    assert options.public_url_domain == pattern.split('.', 1)[1].rstrip('/')
    assert options.container_url_pattern == 'http://localhost:{port}'
    assert DockerLaunchOptions().public_url_domain is None


@pytest.mark.parametrize(
    'pattern',
    [
        '',
        'http://{container_port}-{resource_id}.example.com',
        'https://{container_port}-{resource_id}.example.com:443',
        'https://{container_port}-{resource_id}.example.com/path',
        'https://{container_port}-{resource_id}.example.com?key=private-value',
        'https://{container_port}-{resource_id}.example.com#fragment',
        'https://user:private-value@{container_port}-{resource_id}.example.com',
        'https://{container_port}.example.com/{resource_id}',
        'https://{resource_id}.example.com:{container_port}',
        'https://{resource_id}.{container_port}.example.com',
        'https://{resource_id}-{container_port}',
        'https://{resource_id}-{container_port}.example.com.',
        'https://{resource_id}-{container_port}.*.example.com',
        'https://{resource_id}-{container_port}.bad_domain.example.com',
        'https://{resource_id}-{container_port}.EXAMPLE.com',
        'https://{resource_id}-{port}.example.com',
        'https://{resource_id}-{container_port:05d}.example.com',
        'https://{resource_id!s}-{container_port}.example.com',
        'https://{resource_id}-{resource_id}-{container_port}.example.com',
        'https://{resource_id.__class__}-{container_port}.example.com',
        'https://{resource_id.missing}-{container_port}.example.com',
        'https://{resource_id[bad]}-{container_port}.example.com',
        'https://{resource_id}-{container_port',
        'https://' + 'a' * 30 + '-{resource_id}-{container_port}.example.com',
    ],
)
def test_public_docker_url_rejects_ambiguous_or_unsafe_routing(pattern: str) -> None:
    with pytest.raises(ValidationError) as error:
        DockerLaunchOptions(public_url_pattern=pattern)
    assert 'private-value' not in str(error.value)


@pytest.mark.parametrize(
    'overrides',
    [
        {'working_dir': '/outside'},
        {'initial_env': {'OH_PERSISTENCE_DIR': '/outside'}},
        {'initial_env': {'OH_PERSISTENCE_DIR': '/workspace/../outside'}},
        {'initial_env': {'OH_CONVERSATIONS_PATH': '/outside'}},
        {'initial_env': {'OH_WORKSPACE_PATH': '/outside'}},
        {'initial_env': {'OH_CONVERSATION_WORKTREE_ROOT': '/outside'}},
        {'initial_env': {'OH_BASH_EVENTS_DIR': '/workspace/../outside'}},
        {'initial_env': {'OH_VSCODE_PORT': '9000'}},
        {'command': ['--port', '9000']},
        {'command': ['--port']},
        {'command': ['--port=9000']},
        {'command': ['unsupported-command']},
    ],
)
def test_docker_startup_paths_and_ports_are_validated(
    overrides: dict[str, JsonValue],
) -> None:
    with pytest.raises(ValidationError):
        DockerTemplate.model_validate({'id': 'python', 'image': 'image', **overrides})


def test_direct_model_validation_hides_secret_input() -> None:
    with pytest.raises(ValidationError) as error:
        DockerTemplate.model_validate(
            {
                'id': 'python',
                'image': 'image',
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


def test_explicit_docker_uses_managed_service(
    config_environment: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openhands.app_server.config import config_from_env

    for name, value in docker_env().items():
        monkeypatch.setenv(name, value)
    config = config_from_env()
    assert type(config.sandbox).__name__ == 'ManagedDockerSandboxServiceInjector'
    assert type(config.sandbox_spec).__name__ == 'ConfiguredSandboxSpecServiceInjector'


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
def test_invalid_runtime_catalog(mutation: str) -> None:
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


def test_runtime_model_validation_hides_secret_input() -> None:
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
