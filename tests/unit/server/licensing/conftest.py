import pytest

from tests.unit.license_signing import Signer


@pytest.fixture
def signer() -> Signer:
    return Signer()
