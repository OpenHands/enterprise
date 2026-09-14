"""Shapes used at the python3-saml boundary (verified against version 1.16.0)."""

from collections.abc import Iterator, Mapping
from typing import Literal, Protocol, TypedDict, overload


class SamlElement(Protocol):
    """The lxml element operations used by assertion policy checks."""

    text: str | None

    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator['SamlElement']: ...
    @overload
    def get(self, key: str, default: None = None) -> str | None: ...
    @overload
    def get(self, key: str, default: str) -> str: ...
    def find(
        self, path: str, namespaces: Mapping[str, str] | None = None
    ) -> 'SamlElement | None': ...
    def findall(
        self, path: str, namespaces: Mapping[str, str] | None = None
    ) -> list['SamlElement']: ...
    def findtext(
        self,
        path: str,
        default: None = None,
        namespaces: Mapping[str, str] | None = None,
    ) -> str | None: ...


class SamlEndpoint(TypedDict):
    url: str
    binding: str


class SamlServiceProvider(TypedDict):
    entityId: str
    assertionConsumerService: SamlEndpoint
    NameIDFormat: str
    x509cert: str
    privateKey: str


class SamlCertificates(TypedDict):
    signing: list[str]


class SamlIdentityProvider(TypedDict):
    entityId: str
    singleSignOnService: SamlEndpoint
    x509certMulti: SamlCertificates


class SamlSecurity(TypedDict):
    authnRequestsSigned: bool
    wantAssertionsSigned: bool
    wantMessagesSigned: bool
    wantAssertionsEncrypted: bool
    wantNameId: bool
    wantAttributeStatement: bool
    requestedAuthnContext: bool
    rejectUnsolicitedResponsesWithInResponseTo: bool
    rejectDeprecatedAlgorithm: bool
    signatureAlgorithm: str
    digestAlgorithm: str


class SamlToolkitSettings(TypedDict):
    strict: bool
    debug: bool
    sp: SamlServiceProvider
    idp: SamlIdentityProvider
    security: SamlSecurity


class SamlRequestData(TypedDict):
    https: Literal['on', 'off']
    http_host: str
    server_port: str
    script_name: str
    get_data: dict[str, str]
    post_data: dict[str, str]


class SamlAttributeNameId(TypedDict):
    Format: str | None
    NameQualifier: str | None
    value: str | None


class SamlNestedNameId(TypedDict):
    NameID: SamlAttributeNameId
