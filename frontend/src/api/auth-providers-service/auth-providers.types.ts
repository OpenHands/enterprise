/** The OAuth2 brands ``OAuthProviderDetails.provider_type`` accepts --
 * mirrors ``openhands.app_server.integrations.service_types.ProviderType``.
 * ``enterprise_sso`` is the only one typically used as a login IDP; the rest
 * are git providers.
 */
export type OAuthProviderBrand =
  | "github"
  | "gitlab"
  | "bitbucket"
  | "bitbucket_data_center"
  | "forgejo"
  | "azure_devops"
  | "enterprise_sso";

/** Mirrors ``storage.auth_provider_details.OAuthProviderDetails``.
 *
 * ``client_secret`` is never returned by the server as its real value --
 * only a masked placeholder when one is configured, or ``null`` when it
 * isn't; the two are distinguishable, so there is no separate
 * ``has_client_secret`` flag to maintain.
 */
export interface OAuthProviderDetails {
  kind: "OAuthProviderDetails";
  provider_type: OAuthProviderBrand;
  client_id: string;
  client_secret: string | null;
  authorization_url: string | null;
  token_url: string | null;
  userinfo_url: string | null;
  scopes: string[] | null;
  permitted_drift_seconds: number;
}

/** Mirrors ``storage.auth_provider_details.PasswordProviderDetails`` -- the
 * built-in/integrated password IDP. Carries no fields of its own. */
export interface PasswordProviderDetails {
  kind: "PasswordProviderDetails";
}

/** The two bindings ``SamlProviderDetails.sso_binding`` accepts -- mirrors
 * the backend's ``Literal['redirect', 'post']``. */
export type SamlSsoBinding = "redirect" | "post";

/** Mirrors ``storage.auth_provider_details.SamlProviderDetails``.
 *
 * ``sp_private_key`` is never admin-supplied and never returned by the
 * server as its real value -- the admin API always sends ``null`` for it on
 * create/update (the server auto-generates/carries one forward regardless,
 * see ``server.routes.oauth_providers_admin._ensure_sp_keypair``), and
 * responses only ever echo a masked placeholder when one is configured, or
 * ``null`` when it isn't.
 *
 * ``sp_x509_cert`` is the PEM-encoded, self-signed certificate generated
 * alongside ``sp_private_key`` (same server-side action, see
 * ``storage.auth_provider_details.generate_sp_keypair``) -- it is what the
 * admin copies into the IdP's trusted-SP/ACS signing-certificate
 * configuration, and what our own SP metadata endpoint publishes. ``null``
 * until a key pair has been generated, i.e. until the row has been saved at
 * least once as SAML.
 */
export interface SamlProviderDetails {
  kind: "SamlProviderDetails";
  metadata_url: string | null;
  idp_entity_id: string | null;
  sso_url: string | null;
  sso_binding: SamlSsoBinding;
  slo_url: string | null;
  idp_x509_cert: string | null;
  sp_private_key: string | null;
  sp_x509_cert: string | null;
  name_id_format: string | null;
  attribute_mapping: Record<string, string>;
}

/** Mirrors ``server.services.saml_metadata.SamlIdpMetadata`` -- the
 * ``POST /api/admin/oauth-providers/saml-metadata-preview`` response, used
 * to prefill the rest of the SAML form from a metadata URL. Any field may
 * be ``null`` if the IdP's metadata document didn't declare it. */
export interface SamlIdpMetadata {
  idp_entity_id: string | null;
  sso_url: string | null;
  sso_binding: SamlSsoBinding | null;
  slo_url: string | null;
  idp_x509_cert: string | null;
  name_id_format: string | null;
}

/** Mirrors ``storage.auth_provider_details.AuthProviderDetails``. */
export type AuthProviderDetails =
  | OAuthProviderDetails
  | PasswordProviderDetails
  | SamlProviderDetails;

/** A single ``auth_providers`` row, as returned by the admin API. */
export interface AuthProvider {
  id: number;
  /** ``type(details).__name__`` -- kept here purely for display/filtering;
   * always derived from ``details.kind``, never set independently. */
  kind: string;
  title: string;
  is_idp: boolean;
  details: AuthProviderDetails;
  created_at: string;
  updated_at: string;
}

export interface CreateAuthProviderParams {
  title: string;
  is_idp?: boolean;
  details: AuthProviderDetails;
}

/**
 * Partial update: every field is optional and only the ones present are
 * changed. ``details`` is replaced as a whole blob when present -- set its
 * ``client_secret`` to an empty string to clear an existing secret; omit
 * ``details`` entirely to leave the existing configuration, including its
 * secret, untouched.
 */
export type UpdateAuthProviderParams = Partial<CreateAuthProviderParams>;
