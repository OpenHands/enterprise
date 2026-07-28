import binascii
import hashlib
import json
import logging
from base64 import b64decode, b64encode
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, SecretStr
from sqlalchemy import String, TypeDecorator
from sqlalchemy.engine.interfaces import Dialect

from openhands.sdk.utils.cipher import FERNET_TOKEN_PREFIX, Cipher

_jwt_service = None
_fernet = None
_agent_settings_cipher = None

logger = logging.getLogger(__name__)

# The SDK recognizes encrypted SecretStr leaves by their Fernet prefix before
# delegating decryption to the supplied cipher. Prefix the enterprise JWE with
# the same marker while retaining an unambiguous format discriminator.
_JWT_SETTINGS_CIPHER_PREFIX = f'{FERNET_TOKEN_PREFIX}.jwt.'


def encrypt_value(value: str | SecretStr) -> str:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    return get_jwt_service().encrypt_value(raw)


def decrypt_value(value: str | SecretStr) -> str:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    return get_jwt_service().decrypt_value(raw)


def get_jwt_service():
    from openhands.app_server.config import get_global_config

    global _jwt_service
    if _jwt_service is None:
        jwt_service_injector = get_global_config().jwt
        assert jwt_service_injector is not None
        _jwt_service = jwt_service_injector.get_jwt_service()
    return _jwt_service


def decrypt_legacy_model(decrypt_keys: list, model_instance) -> dict:
    return decrypt_legacy_kwargs(decrypt_keys, model_to_kwargs(model_instance))


def decrypt_legacy_kwargs(encrypt_keys: list, kwargs: dict) -> dict:
    for key, value in kwargs.items():
        try:
            if value is None:
                continue
            if key in encrypt_keys:
                value = decrypt_legacy_value(value)
                kwargs[key] = value
        except binascii.Error:
            pass  # Key is in legacy format...
        except InvalidToken:
            pass  # Key not encrypted...
    return kwargs


def decrypt_legacy_value(value: str | SecretStr) -> str:
    if isinstance(value, SecretStr):
        return (
            get_fernet().decrypt(b64decode(value.get_secret_value().encode())).decode()
        )
    else:
        return get_fernet().decrypt(b64decode(value.encode())).decode()


def encrypt_legacy_value(value: str | SecretStr) -> str:
    if isinstance(value, SecretStr):
        return b64encode(
            get_fernet().encrypt(value.get_secret_value().encode())
        ).decode()
    else:
        return b64encode(get_fernet().encrypt(value.encode())).decode()


def get_fernet():
    global _fernet
    if _fernet is None:
        jwt_svc = get_jwt_service()
        default_key = jwt_svc.get_key(jwt_svc._default_key_id)
        secret = default_key.key.get_secret_value()
        fernet_key = b64encode(hashlib.sha256(secret.encode()).digest())
        _fernet = Fernet(fernet_key)
    return _fernet


class JwtSettingsCipher(Cipher):
    """SDK cipher backed by the enterprise key-ID-aware JWT service.

    Agent settings use the SDK's field serializers to encrypt only SecretStr
    leaves. The enterprise JWT service supplies the actual encryption so each
    value carries a key ID and continues to decrypt across key rotation.
    """

    def __init__(self) -> None:
        jwt_svc = get_jwt_service()
        default_key = jwt_svc.get_key(jwt_svc._default_key_id)
        # Keep support for ordinary SDK Fernet ciphertext if a snapshot was
        # produced by a non-enterprise settings store.
        super().__init__(default_key.key.get_secret_value())

    def encrypt(self, secret: SecretStr | None) -> str | None:
        if secret is None:
            return None
        encrypted = get_jwt_service().encrypt_value(secret.get_secret_value())
        return f'{_JWT_SETTINGS_CIPHER_PREFIX}{encrypted}'

    def decrypt(self, secret: str | None) -> SecretStr | None:
        if secret is None:
            return None
        if not secret.startswith(_JWT_SETTINGS_CIPHER_PREFIX):
            return super().decrypt(secret)
        try:
            encrypted = secret.removeprefix(_JWT_SETTINGS_CIPHER_PREFIX)
            return SecretStr(get_jwt_service().decrypt_value(encrypted))
        except Exception as exc:
            logger.warning(
                'Failed to decrypt an agent-settings secret; setting it to None: %s',
                exc,
            )
            return None

    def try_decrypt_str(self, value: str) -> str | None:
        if not value.startswith(_JWT_SETTINGS_CIPHER_PREFIX):
            return super().try_decrypt_str(value)
        try:
            encrypted = value.removeprefix(_JWT_SETTINGS_CIPHER_PREFIX)
            return get_jwt_service().decrypt_value(encrypted)
        except Exception:
            return None


def get_agent_settings_cipher() -> Cipher:
    """Return the process-wide cipher for SDK agent-settings serialization."""
    global _agent_settings_cipher
    if _agent_settings_cipher is None:
        _agent_settings_cipher = JwtSettingsCipher()
    return _agent_settings_cipher


def model_to_kwargs(model_instance):
    return {
        column.name: getattr(model_instance, column.name)
        for column in model_instance.__table__.columns
    }


class EncryptedJSON(TypeDecorator[dict[str, Any]]):
    """JSON column whose serialized payload is encrypted at rest.

    Accepts either a plain ``dict`` or a pydantic ``BaseModel``. Pydantic
    models are dumped via ``model_dump(mode='json', context={'expose_secrets': True})``
    so nested ``SecretStr`` values keep their real payload — the column
    itself is the encryption boundary, so masking on the way in would
    corrupt round-trips.

    Use for JSON payloads that may contain secrets (e.g. nested ``api_key``
    fields) where the existing ``_<field>`` String + property pattern is
    awkward — this keeps the column accessible as a normal ORM attribute
    while encrypting the entire JSON blob via the same JWE service used
    by ``encrypt_value``/``decrypt_value``.
    """

    impl = String
    cache_ok = True

    def process_bind_param(
        self, value: BaseModel | dict[str, Any] | None, dialect: Dialect
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            value = value.model_dump(mode='json', context={'expose_secrets': True})
        return encrypt_value(json.dumps(value))

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        return json.loads(decrypt_value(value))
