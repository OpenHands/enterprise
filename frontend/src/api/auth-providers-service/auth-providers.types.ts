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

/** Mirrors ``storage.auth_provider_details.AuthProviderDetails``. SAML
 * support is a follow-up (``SamlProviderDetails`` on the backend). */
export type AuthProviderDetails =
  | OAuthProviderDetails
  | PasswordProviderDetails;

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
