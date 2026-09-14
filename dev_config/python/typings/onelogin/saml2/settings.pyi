from server.auth.saml_types import SamlToolkitSettings

class OneLogin_Saml2_Settings:
    def __init__(
        self,
        settings: SamlToolkitSettings,
        custom_base_path: str | None = ...,
        sp_validation_only: bool = ...,
    ) -> None: ...
    def get_sp_metadata(self) -> str | bytes: ...
    def validate_metadata(self, xml: str | bytes) -> list[str]: ...
