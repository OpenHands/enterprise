import { Provider, ProviderToken } from "#/types/settings";

export type CustomSecret = {
  name: string;
  value: string;
  description?: string;
};

/** Scope of a custom secret — personal (owned by the user) or organization. */
export type CustomSecretScope = "personal" | "organization";

export type CustomSecretWithoutValue = Omit<CustomSecret, "value"> & {
  /** Whether the secret is personal or shared across the organization.
   * Personal secrets are owned by the current user; organization secrets
   * are shared org-wide and editable only by admins/owners. The backend
   * always returns this field in search/list responses. */
  scope?: CustomSecretScope;
};

/** Paginated response from GET /api/v1/secrets/search */
export interface CustomSecretPage {
  items: CustomSecretWithoutValue[];
  next_page_id: string | null;
}

/** @deprecated Use CustomSecretPage instead */
export interface GetSecretsResponse {
  custom_secrets: CustomSecretWithoutValue[];
}

export interface POSTProviderTokens {
  provider_tokens: Record<Provider, ProviderToken>;
}

export interface SearchSecretsParams {
  name__contains?: string;
  page_id?: string;
  limit?: number;
}
