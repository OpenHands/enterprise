from __future__ import annotations

import pytest

from server.licensing import service as service_module
from server.licensing.config import LicenseConfig
from server.licensing.service import LicenseService
from server.licensing.sso import SsoNotLicensedError, check_sso_licensed, is_sso_login
from storage.auth_provider_details import OAuthProviderDetails, SamlProviderDetails
from tests.unit.license_signing import LICENSE_KEY


def _install(monkeypatch, **config) -> LicenseService:
    values = {
        'enforcement_enabled': True,
        'license_key': LICENSE_KEY,
        'license_server_url': 'https://cc.example',
        'check_interval_seconds': 1800,
    }
    values.update(config)
    service = LicenseService(LicenseConfig(**values))
    monkeypatch.setattr(service_module, '_license_service', service)
    return service


async def test_unlicensed_install_has_no_sso(monkeypatch):
    service = _install(monkeypatch, license_key=None)
    await service.check_now()

    with pytest.raises(SsoNotLicensedError):
        check_sso_licensed()


def test_sso_is_allowed_while_enforcement_is_off(monkeypatch):
    _install(monkeypatch, enforcement_enabled=False)

    check_sso_licensed()


SAML = SamlProviderDetails(idp_entity_id='https://idp.example.com')
GITHUB = OAuthProviderDetails(provider_type='github', client_id='cid')


@pytest.mark.parametrize(
    ('is_idp', 'enabled', 'details', 'expected'),
    [
        (True, True, SAML, True),
        (True, False, SAML, False),
        (False, True, SAML, False),
        (True, True, GITHUB, False),
    ],
)
def test_is_sso_login(is_idp, enabled, details, expected):
    assert is_sso_login(is_idp=is_idp, enabled=enabled, details=details) is expected
