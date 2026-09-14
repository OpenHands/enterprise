from onelogin.saml2.settings import OneLogin_Saml2_Settings

from server.auth.saml_types import SamlRequestData, SamlToolkitSettings

class OneLogin_Saml2_Auth:
    def __init__(
        self,
        request_data: SamlRequestData,
        old_settings: SamlToolkitSettings | OneLogin_Saml2_Settings | None = ...,
        custom_base_path: str | None = ...,
    ) -> None: ...
    def login(
        self,
        return_to: str | None = ...,
        force_authn: bool = ...,
        is_passive: bool = ...,
        set_nameid_policy: bool = ...,
        name_id_value_req: str | None = ...,
    ) -> str: ...
    def get_last_request_id(self) -> str | None: ...
