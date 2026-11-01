import pytest

from tests.unit.license_signing import Signer


@pytest.fixture
def signer(monkeypatch) -> Signer:
    signer = Signer()
    signer.trust(monkeypatch)
    return signer
