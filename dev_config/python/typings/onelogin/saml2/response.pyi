from onelogin.saml2.settings import OneLogin_Saml2_Settings

from server.auth.saml_types import SamlElement, SamlNestedNameId, SamlRequestData

class OneLogin_Saml2_Response:
    document: SamlElement
    decrypted_document: SamlElement | None
    encrypted: bool
    def __init__(self, settings: OneLogin_Saml2_Settings, response: str) -> None: ...
    def is_valid(
        self,
        request_data: SamlRequestData,
        request_id: str | None = ...,
        raise_exceptions: bool = ...,
    ) -> bool: ...
    def get_in_response_to(self) -> str | None: ...
    def get_attributes(self) -> dict[str, list[str | SamlNestedNameId]]: ...
    def _decrypt_assertion(self, xml: SamlElement) -> SamlElement: ...
