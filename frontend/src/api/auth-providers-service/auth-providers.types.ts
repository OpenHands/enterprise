/** The OAuth2 brands ``OAuthProviderDetails.provider_type`` accepts --
 * mirrors ``openhands.app_server.integrations.service_types.ProviderType``.
 * ``enterprise_sso``, ``jira`` and ``jira_dc`` are login/token-only brands;
 * the rest are git providers.
 */
export type OAuthProviderBrand =
  | "github"
  | "gitlab"
  | "bitbucket"
  | "bitbucket_data_center"
  | "forgejo"
  | "azure_devops"
  | "enterprise_sso"
  | "jira"
  | "jira_dc";

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
  client_secret?: string | null;
  authorization_url: string | null;
  token_url: string | null;
  userinfo_url: string | null;
  scopes: string[] | null;
  permitted_drift_seconds: number;
  /** For a Keycloak-federated login row: the git provider whose real token
   * is additionally fetched from Keycloak after login. ``null`` otherwise. */
  legacy_keycloak_type: Exclude<
    OAuthProviderBrand,
    "enterprise_sso" | "jira" | "jira_dc"
  > | null;
  /** Conversation secret name a linked user's access token is exposed
   * under (with OAuth v2 login enabled). ``null`` keeps it out. */
  named_secret: string | null;
  /** Identity field (``sub`` / ``email`` / ``email_verified``) -> the
   * userinfo / ID-token claim the provider sends it as. Empty uses the OIDC
   * defaults. */
  attribute_mapping: Record<string, string>;
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

/** Default ``color`` for a row that hasn't been given a brand color yet --
 * mirrors ``storage.auth_provider.DEFAULT_AUTH_PROVIDER_COLOR``. */
export const DEFAULT_AUTH_PROVIDER_COLOR = "#000000";

/** A single ``auth_providers`` row, as returned by the admin API. */
export interface AuthProvider {
  id: number;
  /** ``type(details).__name__`` -- kept here purely for display/filtering;
   * always derived from ``details.kind``, never set independently. */
  kind: string;
  title: string;
  is_idp: boolean;
  /** Raw SVG markup for this provider's login button. ``null`` until an
   * admin sets one -- the frontend falls back to a generic icon. */
  icon: string | null;
  /** CSS color string for this provider's login button. */
  color: string;
  /** Whether this provider may be used for a new login or link. New rows
   * start disabled (the server defaults it to ``false``) so a
   * half-configured provider -- e.g. a preset with placeholder URLs -- does
   * not appear on the login screen before an admin finishes it. Disabling a
   * provider only blocks new flows; sessions and tokens already minted
   * through it keep working. */
  enabled: boolean;
  /** Whether a brand-new user may be created the first time someone
   * authenticates through this provider, or whether only an already-linked
   * account may sign in through it. */
  allow_registration: boolean;
  /** Initial value of a new user's `allow_match_by_email` flag when they are
   * first created by a login through this provider -- see the backend's
   * `server.auth.provider_identity.resolve_or_create_user`. */
  allow_match_by_email: boolean;
  details: AuthProviderDetails;
  created_at: string;
  updated_at: string;
}

export interface CreateAuthProviderParams {
  title: string;
  is_idp?: boolean;
  icon?: string | null;
  color?: string;
  /** Defaults to ``false`` server-side: enable a provider only once it is
   * fully configured. */
  enabled?: boolean;
  allow_registration?: boolean;
  allow_match_by_email?: boolean;
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

/** A single login IDP as returned by the public, unauthenticated
 * ``GET /oauth/idp-providers`` endpoint -- backs the "Let's Get Started"
 * login screen's provider buttons when ``ENABLE_OAUTH_V2_LOGIN`` is on.
 * Never carries secrets, non-IDP (git provider) rows, or anything from
 * ``details`` beyond the OAuth brand. */
export interface PublicIdpProvider {
  id: number;
  title: string;
  icon: string | null;
  color: string;
  provider_type: OAuthProviderBrand | null;
}

/** A single ``auth_provider_users`` row for one provider, joined with the
 * linked ``User`` -- mirrors
 * ``server.routes.oauth_providers_admin.AuthProviderUserResponse``. Backs
 * the admin "User Links" table
 * (``/super-admin/auth-providers/{provider_id}/users``). */
export interface AuthProviderUserLink {
  id: number;
  user_id: string;
  email: string | null;
  /** The linked account's display name (git profile name, else the local
   * part of its email) -- what the admin recognizes the account by. */
  user_name: string | null;
  external_subject_id: string;
  external_email: string | null;
}

/** Paginated response for ``GET /{provider_id}/users``. */
export interface AuthProviderUserLinkPage {
  items: AuthProviderUserLink[];
  total: number;
  current_page: number;
  per_page: number;
}
