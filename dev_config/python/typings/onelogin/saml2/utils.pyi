from server.auth.saml_types import SamlElement

class OneLogin_Saml2_Utils:
    @staticmethod
    def parse_SAML_to_time(timestr: str) -> int: ...
    @staticmethod
    def decode_base64_and_inflate(
        value: str | bytes, ignore_zip: bool = ...
    ) -> bytes: ...
    @staticmethod
    def add_sign(
        xml: str | bytes | SamlElement,
        key: str,
        cert: str,
        debug: bool = ...,
        sign_algorithm: str = ...,
        digest_algorithm: str = ...,
    ) -> bytes: ...
    @staticmethod
    def validate_sign(
        xml: str | bytes | SamlElement,
        cert: str | None = ...,
        fingerprint: str | None = ...,
        fingerprintalg: str = ...,
        validatecert: bool = ...,
        debug: bool = ...,
        xpath: str | None = ...,
        multicerts: list[str] | None = ...,
        raise_exceptions: bool = ...,
    ) -> bool: ...
