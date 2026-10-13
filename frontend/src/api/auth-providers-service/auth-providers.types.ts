/** A single ``oauth_providers`` row, as returned by the admin API.
 *
 * ``client_secret`` is never returned by the server -- only whether one is
 * configured (``has_client_secret``).
 */
export interface OAuthProvider {
  id: number;
  provider_category: string;
  display_name: string;
  is_idp: boolean;
  client_id: string;
  has_client_secret: boolean;
  authorization_url: string | null;
  token_url: string | null;
  userinfo_url: string | null;
  scopes: string[] | null;
  permitted_drift_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface CreateOAuthProviderParams {
  provider_category: string;
  display_name: string;
  is_idp?: boolean;
  client_id: string;
  /** Omit to leave unset; empty string is treated the same as omitted. */
  client_secret?: string | null;
  authorization_url?: string | null;
  token_url?: string | null;
  userinfo_url?: string | null;
  scopes?: string[] | null;
  permitted_drift_seconds?: number;
}

/**
 * Partial update: every field is optional and only the ones present are
 * changed. Set ``client_secret`` to an empty string to clear an existing
 * secret; omit the field entirely to leave it untouched.
 */
export type UpdateOAuthProviderParams = Partial<CreateOAuthProviderParams>;
