import pytest

from server.org_defaults_config import get_org_defaults_condenser_config

_ENV_KEYS = [
    'OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS',
    'OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING',
    'OPENHANDS_ORG_DEFAULTS_CONDENSER_OVERWRITE_EXISTING',
]


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_org_defaults_condenser_config_defaults_to_noop():
    config = get_org_defaults_condenser_config()

    assert config.max_tokens is None
    assert config.apply_to_existing is False
    assert config.overwrite_existing is False


def test_org_defaults_condenser_config_accepts_strict_values(monkeypatch):
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS', '200000')
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_APPLY_TO_EXISTING', 'true')
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_OVERWRITE_EXISTING', '1')

    config = get_org_defaults_condenser_config()

    assert config.max_tokens == 200000
    assert config.apply_to_existing is True
    assert config.overwrite_existing is True


@pytest.mark.parametrize('value', ['0', '-1', '12.5', 'abc'])
def test_org_defaults_condenser_config_rejects_invalid_max_tokens(monkeypatch, value):
    monkeypatch.setenv('OPENHANDS_ORG_DEFAULTS_CONDENSER_MAX_TOKENS', value)

    with pytest.raises(ValueError):
        get_org_defaults_condenser_config()


@pytest.mark.parametrize('key', _ENV_KEYS[1:])
def test_org_defaults_condenser_config_rejects_invalid_booleans(monkeypatch, key):
    monkeypatch.setenv(key, 'tru')

    with pytest.raises(ValueError):
        get_org_defaults_condenser_config()
