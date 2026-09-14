"""SAML verification and browser-bound completion using PostgreSQL state."""

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hmac import compare_digest
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from server.auth.native_password import NativeAuthError, normalize_email
from server.auth.native_session import digest_token, new_token
from server.auth.native_types import SessionFactory
from server.auth.saml_config import PERSISTENT_NAME_ID, SamlSettings, get_saml_settings
from server.auth.saml_types import SamlElement, SamlRequestData
from server.services.native_auth_service import (
    NativeAuthService,
    NativeLogin,
    safe_return_path,
)
from storage.database import a_session_maker
from storage.native_saml import SamlReplay, SamlTransaction

TRANSACTION_SECONDS = 600
MAX_RESPONSE_BYTES = 256 * 1024
NS = {
    'saml': 'urn:oasis:names:tc:SAML:2.0:assertion',
    'ds': 'http://www.w3.org/2000/09/xmldsig#',
}
SIGNATURE_ALGORITHMS = {
    f'http://www.w3.org/2001/04/xmldsig-more#rsa-sha{bits}' for bits in (256, 384, 512)
}
DIGEST_ALGORITHMS = {
    'http://www.w3.org/2001/04/xmlenc#sha256',
    'http://www.w3.org/2001/04/xmldsig-more#sha384',
    'http://www.w3.org/2001/04/xmlenc#sha512',
}
EXCLUSIVE_C14N = 'http://www.w3.org/2001/10/xml-exc-c14n#'
TRANSFORMS = {EXCLUSIVE_C14N, 'http://www.w3.org/2000/09/xmldsig#enveloped-signature'}


def _safe_signature_policy(document: SamlElement) -> None:
    """Disallow executable XSLT/XPath transforms before invoking xmlsec."""
    if len(document.findall('.//ds:Signature', namespaces=NS)) > 2:
        raise _invalid()
    for transforms in document.findall('.//ds:Transforms', namespaces=NS):
        if not 1 <= len(transforms) <= 2:
            raise _invalid()
    for tag, allowed in (
        ('Transform', TRANSFORMS),
        ('CanonicalizationMethod', {EXCLUSIVE_C14N}),
        ('SignatureMethod', SIGNATURE_ALGORITHMS),
        ('DigestMethod', DIGEST_ALGORITHMS),
    ):
        if any(
            node.get('Algorithm') not in allowed
            for node in document.findall(f'.//ds:{tag}', namespaces=NS)
        ):
            raise _invalid()
    for reference in document.findall('.//ds:Reference', namespaces=NS):
        if not reference.get('URI', '').startswith('#'):
            raise _invalid()
    if document.findall('.//ds:RetrievalMethod', namespaces=NS):
        raise _invalid()
    encryption_ns = {'xenc': 'http://www.w3.org/2001/04/xmlenc#'}
    if document.findall('.//xenc:CipherReference', namespaces=encryption_ns):
        raise _invalid()
    encryption_algorithms = {
        'http://www.w3.org/2001/04/xmlenc#aes128-cbc',
        'http://www.w3.org/2001/04/xmlenc#aes256-cbc',
        'http://www.w3.org/2009/xmlenc11#aes128-gcm',
        'http://www.w3.org/2009/xmlenc11#aes256-gcm',
        'http://www.w3.org/2001/04/xmlenc#rsa-oaep-mgf1p',
    }
    if any(
        node.get('Algorithm') not in encryption_algorithms
        for node in document.findall(
            './/xenc:EncryptionMethod', namespaces=encryption_ns
        )
    ):
        raise _invalid()


def _invalid() -> NativeAuthError:
    return NativeAuthError('SSO sign-in could not be completed. Start again.', 400)


def _now() -> datetime:
    return datetime.now(UTC)


def _request_data(config: SamlSettings) -> SamlRequestData:
    url = urlsplit(config.acs_url)
    return {
        'https': 'on' if url.scheme == 'https' else 'off',
        'http_host': url.netloc,
        'server_port': str(url.port or (443 if url.scheme == 'https' else 80)),
        'script_name': url.path,
        'get_data': {},
        'post_data': {},
    }


def _configuration_digest(config: SamlSettings) -> str:
    # In-flight state must not survive a trust/admission-policy replacement.
    data = config.toolkit_settings() | {
        'connection': config.connection_id,
        'email_attribute': config.email_attribute,
        'allow_jit': config.allow_jit,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class VerifiedSaml:
    subject: str
    email: str = field(repr=False)
    message_id: str
    assertion_id: str
    expires_at: datetime
    replay_expires_at: datetime
    auth_time: datetime
    session_expiry_bound: datetime | None


def verify_saml_response(
    config: SamlSettings,
    encoded: str,
    request_id: str,
    *,
    require_authenticated_after: datetime | None = None,
) -> VerifiedSaml:
    """The toolkit validates XML/signatures; tighten its optional profile fields.

    Never select identity data until the library accepts the assertion signature.
    Its default checks permit absent fields and prefix destination matching, so
    the SP additionally requires exact web-SSO profile values on verified XML.
    """
    from onelogin.saml2.constants import OneLogin_Saml2_Constants
    from onelogin.saml2.response import OneLogin_Saml2_Response
    from onelogin.saml2.settings import OneLogin_Saml2_Settings
    from onelogin.saml2.utils import OneLogin_Saml2_Utils
    from onelogin.saml2.xml_utils import OneLogin_Saml2_XML

    try:
        if not encoded or len(encoded) > MAX_RESPONSE_BYTES * 2:
            raise _invalid()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise _invalid()
        # Constructor decrypts EncryptedAssertion. Reject unsafe transforms and
        # external references on bounded, toolkit-hardened XML before that step.
        _safe_signature_policy(OneLogin_Saml2_XML.to_etree(raw))
        response = OneLogin_Saml2_Response(
            OneLogin_Saml2_Settings(config.toolkit_settings()), encoded
        )
        _safe_signature_policy(response.document)
        if response.encrypted:
            if response.decrypted_document is None:
                raise _invalid()
            _safe_signature_policy(response.decrypted_document)
        if not response.is_valid(_request_data(config), request_id=request_id):
            raise _invalid()
        document = (
            response.decrypted_document if response.encrypted else response.document
        )
        if document is None:
            raise _invalid()
        if (
            document.get('Destination') != config.acs_url
            or response.get_in_response_to() != request_id
            or document.findtext('saml:Issuer', namespaces=NS) != config.issuer
        ):
            raise _invalid()
        assertions = document.findall('saml:Assertion', namespaces=NS)
        if len(assertions) != 1:
            raise _invalid()
        assertion = assertions[0]
        if assertion.findtext('saml:Issuer', namespaces=NS) != config.issuer:
            raise _invalid()
        now = _now().timestamp()
        statements = assertion.findall('saml:AuthnStatement', namespaces=NS)
        if len(statements) != 1:
            raise _invalid()
        statement = statements[0]
        authenticated = OneLogin_Saml2_Utils.parse_SAML_to_time(
            statement.get('AuthnInstant', '')
        )
        if authenticated > now + 60 or (
            require_authenticated_after is not None
            and authenticated < require_authenticated_after.timestamp() - 60
        ):
            raise _invalid()
        session_expiry = None
        if statement.get('SessionNotOnOrAfter') is not None:
            session_expiry = OneLogin_Saml2_Utils.parse_SAML_to_time(
                statement.get('SessionNotOnOrAfter', '')
            )
            if session_expiry <= now:
                raise _invalid()
        issued = OneLogin_Saml2_Utils.parse_SAML_to_time(
            assertion.get('IssueInstant', '')
        )
        if issued > now + 60 or issued < now - TRANSACTION_SECONDS:
            raise _invalid()
        conditions = assertion.find('saml:Conditions', namespaces=NS)
        if conditions is None:
            raise _invalid()
        not_before = OneLogin_Saml2_Utils.parse_SAML_to_time(
            conditions.get('NotBefore', '')
        )
        expires = OneLogin_Saml2_Utils.parse_SAML_to_time(
            conditions.get('NotOnOrAfter', '')
        )
        if not_before > now + 60 or expires <= now:
            raise _invalid()
        restrictions = conditions.findall('saml:AudienceRestriction', namespaces=NS)
        if not restrictions or any(
            config.entity_id not in [a.text for a in restriction]
            for restriction in restrictions
        ):
            raise _invalid()
        confirmations = assertion.findall(
            'saml:Subject/saml:SubjectConfirmation', namespaces=NS
        )
        valid_confirmations = []
        for confirmation in confirmations:
            data = confirmation.find('saml:SubjectConfirmationData', namespaces=NS)
            if (
                confirmation.get('Method') != OneLogin_Saml2_Constants.CM_BEARER
                or data is None
                or data.get('Recipient') != config.acs_url
                or data.get('InResponseTo') != request_id
                or data.get('NotBefore') is not None
            ):
                continue
            end = OneLogin_Saml2_Utils.parse_SAML_to_time(data.get('NotOnOrAfter', ''))
            if end > now:
                valid_confirmations.append(end)
        if not valid_confirmations:
            raise _invalid()
        replay_expiry = max(expires, max(valid_confirmations)) + 60
        expires = min(expires, min(valid_confirmations))
        nameids = assertion.findall('saml:Subject/saml:NameID', namespaces=NS)
        if len(nameids) != 1:
            raise _invalid()
        nameid = nameids[0]
        if (
            nameid.get('Format') != PERSISTENT_NAME_ID
            or not nameid.text
            or len(nameid.text) > 1024
            or nameid.get('SPProvidedID') is not None
            or nameid.get('NameQualifier', config.issuer) != config.issuer
            or nameid.get('SPNameQualifier', config.entity_id) != config.entity_id
        ):
            raise _invalid()
        subject = hashlib.sha256(
            json.dumps(
                [PERSISTENT_NAME_ID, config.issuer, config.entity_id, nameid.text],
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode()
        ).hexdigest()
        attributes = response.get_attributes()
        emails = attributes.get(config.email_attribute, [])
        if len(emails) != 1 or not isinstance(emails[0], str):
            raise _invalid()
        normalize_email(emails[0])
        message_id, assertion_id = document.get('ID'), assertion.get('ID')
        if (
            not message_id
            or not assertion_id
            or message_id == assertion_id
            or len(message_id) > 512
            or len(assertion_id) > 512
        ):
            raise _invalid()
        return VerifiedSaml(
            subject,
            emails[0],
            message_id,
            assertion_id,
            datetime.fromtimestamp(expires, UTC),
            datetime.fromtimestamp(replay_expiry, UTC),
            datetime.fromtimestamp(authenticated, UTC),
            datetime.fromtimestamp(session_expiry, UTC) if session_expiry else None,
        )
    except Exception:
        # Toolkit exceptions can embed XML, attributes or subject identifiers.
        raise _invalid() from None


class NativeSamlService:
    def __init__(
        self,
        session_factory: SessionFactory | None = None,
        settings: SamlSettings | None = None,
    ) -> None:
        self.sessions = session_factory or a_session_maker
        self.settings = settings
        self.verification_slots = asyncio.Semaphore(4)

    def config(self) -> SamlSettings:
        config = self.settings or get_saml_settings()
        if config is None:
            raise NativeAuthError('Not found', 404)
        return config

    async def cleanup_expired_state(self) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(SamlTransaction).where(SamlTransaction.expires_at < _now())
            )
            await session.execute(
                delete(SamlReplay).where(SamlReplay.expires_at < _now())
            )

    async def start(
        self,
        *,
        return_path: str | None = None,
        invitation_token: str | None = None,
        link: bool = False,
        reauthenticate: bool = False,
        session_token: str | None = None,
    ) -> tuple[str, str]:
        config = self.config()
        principal = None
        if link:
            principal = await NativeAuthService(self.sessions).authenticate_session(
                session_token or ''
            )
            if principal is None or principal.session_id is None:
                raise NativeAuthError(
                    'Recent browser authentication required',
                    401,
                    code='recent_auth_required',
                )
            from server.auth.auth_config import get_native_auth_settings

            if principal.auth_time is None or principal.auth_time < _now() - timedelta(
                seconds=get_native_auth_settings().recent_auth_seconds
            ):
                raise NativeAuthError(
                    'Recent browser authentication required',
                    403,
                    code='recent_auth_required',
                )
        browser, relay = new_token(), new_token()
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        toolkit = OneLogin_Saml2_Auth(
            _request_data(config), old_settings=config.toolkit_settings()
        )
        started_at = _now()
        location = await run_in_threadpool(
            toolkit.login, return_to=relay, force_authn=link or reauthenticate
        )
        request_id = toolkit.get_last_request_id()
        if not request_id:
            raise _invalid()
        async with self.sessions() as session, session.begin():
            await session.execute(
                delete(SamlTransaction).where(SamlTransaction.expires_at < _now())
            )
            await session.execute(
                delete(SamlReplay).where(SamlReplay.expires_at < _now())
            )
            session.add(
                SamlTransaction(
                    relay_digest=digest_token(relay, 'saml-relay'),
                    browser_digest=digest_token(browser, 'saml-browser'),
                    request_id=request_id,
                    connection_id=config.connection_id,
                    configuration_digest=_configuration_digest(config),
                    return_path=safe_return_path(return_path),
                    context={
                        'invitation_token': invitation_token,
                        'reauthenticate': link or reauthenticate,
                        'started_at': started_at.isoformat(),
                    },
                    link_account_id=principal.account_id if principal else None,
                    link_session_id=principal.session_id if principal else None,
                    expires_at=started_at + timedelta(seconds=TRANSACTION_SECONDS),
                )
            )
        return location, browser

    async def accept_response(self, relay: str, encoded: str) -> None:
        config = self.config()
        if not relay or len(relay) > 128:
            raise _invalid()
        if self.verification_slots.locked():
            raise NativeAuthError('SSO verification is busy; start again.', 429)
        async with self.verification_slots, self.sessions() as session, session.begin():
            transaction = await session.scalar(
                select(SamlTransaction)
                .where(
                    SamlTransaction.relay_digest == digest_token(relay, 'saml-relay')
                )
                .with_for_update()
            )
            if (
                transaction is None
                or transaction.expires_at <= _now()
                or transaction.consumed_at is not None
                or transaction.verified_claims is not None
                or transaction.configuration_digest != _configuration_digest(config)
            ):
                raise _invalid()
            verified = await run_in_threadpool(
                verify_saml_response,
                config,
                encoded,
                transaction.request_id,
                require_authenticated_after=(
                    datetime.fromisoformat(transaction.context['started_at'])
                    if transaction.context['reauthenticate']
                    else None
                ),
            )
            for identifier in (verified.message_id, verified.assertion_id):
                digest = digest_token(
                    f'{config.connection_id}:{config.issuer}:{identifier}',
                    'saml-replay',
                )
                claimed = await session.scalar(
                    insert(SamlReplay)
                    .values(
                        id_digest=digest,
                        expires_at=max(
                            verified.replay_expires_at,
                            _now() + timedelta(seconds=TRANSACTION_SECONDS + 60),
                        ),
                    )
                    .on_conflict_do_nothing()
                    .returning(SamlReplay.id_digest)
                )
                if claimed is None:
                    raise _invalid()
            transaction.verified_claims = {
                'subject': verified.subject,
                'email': verified.email,
                'auth_time': verified.auth_time.isoformat(),
                'session_expiry_bound': verified.session_expiry_bound.isoformat()
                if verified.session_expiry_bound
                else None,
            }
            transaction.expires_at = min(transaction.expires_at, verified.expires_at)

    async def complete(
        self, browser: str | None, session_token: str | None
    ) -> NativeLogin:
        config = self.config()
        if not browser or len(browser) > 128:
            raise _invalid()
        async with self.sessions() as session, session.begin():
            transaction = await session.scalar(
                select(SamlTransaction)
                .where(
                    SamlTransaction.browser_digest
                    == digest_token(browser, 'saml-browser')
                )
                .with_for_update()
            )
            if (
                transaction is None
                or transaction.expires_at <= _now()
                or transaction.consumed_at is not None
                or transaction.verified_claims is None
                or not compare_digest(
                    transaction.configuration_digest, _configuration_digest(config)
                )
            ):
                raise _invalid()
            claims = transaction.verified_claims
            invitation = transaction.context.get('invitation_token')
            account_id, session_id, return_path = (
                transaction.link_account_id,
                transaction.link_session_id,
                transaction.return_path,
            )
            transaction.consumed_at = _now()
            transaction.verified_claims = None
            transaction.context = {}
        # Claim commits first: failures require a new SSO flow; concurrent workers
        # can never complete the same transaction twice. Identity/session writes
        # are one transaction in the shared account completion service.
        result = await NativeAuthService(self.sessions).complete_federated_login(
            connection_id=config.connection_id,
            issuer=config.issuer,
            subject=claims['subject'],
            email=claims['email'],
            return_path=return_path,
            invitation_token=invitation,
            link_account_id=account_id,
            link_session_id=session_id,
            link_session_token=session_token if account_id is not None else None,
            allow_jit=config.allow_jit,
            auth_method='saml',
            auth_time=datetime.fromisoformat(claims['auth_time']),
            session_expiry_bound=datetime.fromisoformat(claims['session_expiry_bound'])
            if claims['session_expiry_bound']
            else None,
        )
        if session_token:
            await NativeAuthService(self.sessions).revoke_session(session_token)
        return result


@lru_cache(maxsize=1)
def get_native_saml_service() -> NativeSamlService:
    return NativeSamlService()
