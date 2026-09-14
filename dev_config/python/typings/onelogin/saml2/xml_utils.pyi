from server.auth.saml_types import SamlElement

class OneLogin_Saml2_XML:
    @staticmethod
    def to_etree(xml: str | bytes | SamlElement) -> SamlElement: ...
