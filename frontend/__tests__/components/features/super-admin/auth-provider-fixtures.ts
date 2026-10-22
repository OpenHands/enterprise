import type { AuthProvider } from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";

export const GITHUB_PROVIDER: AuthProvider = {
  id: 1,
  kind: "OAuthProviderDetails",
  title: "GitHub",
  is_idp: false,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "github",
    client_id: "github-client-id",
    client_secret: "**********",
    authorization_url: null,
    token_url: null,
    userinfo_url: null,
    scopes: ["repo"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: null,
  },
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

export const KEYCLOAK_GITHUB_LOGIN_PROVIDER: AuthProvider = {
  id: 4,
  kind: "OAuthProviderDetails",
  title: "Log in with GitHub",
  is_idp: true,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "OAuthProviderDetails",
    provider_type: "enterprise_sso",
    client_id: "keycloak-client-id",
    client_secret: "**********",
    authorization_url: "https://auth.example.com/auth?kc_idp_hint=github",
    token_url: "https://auth.example.com/token",
    userinfo_url: "https://auth.example.com/userinfo",
    scopes: ["openid", "email", "profile"],
    permitted_drift_seconds: 60,
    legacy_keycloak_type: "github",
  },
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

export const PASSWORD_PROVIDER: AuthProvider = {
  id: 2,
  kind: "PasswordProviderDetails",
  title: "Password Login",
  is_idp: true,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: { kind: "PasswordProviderDetails" },
  created_at: "2024-01-02T00:00:00Z",
  updated_at: "2024-01-02T00:00:00Z",
};

export const SAML_PROVIDER: AuthProvider = {
  id: 3,
  kind: "SamlProviderDetails",
  title: "Okta SAML",
  is_idp: true,
  icon: null,
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  allow_registration: true,
  allow_match_by_email: true,
  details: {
    kind: "SamlProviderDetails",
    metadata_url: "https://idp.example.com/metadata",
    idp_entity_id: "https://idp.example.com/entity",
    sso_url: "https://idp.example.com/sso",
    sso_binding: "redirect",
    slo_url: "https://idp.example.com/slo",
    idp_x509_cert:
      "-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----",
    sp_private_key: "**********",
    sp_x509_cert:
      "-----BEGIN CERTIFICATE-----\nMIIC...\n-----END CERTIFICATE-----",
    name_id_format: "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
    attribute_mapping: { email: "mail", name: "displayName" },
  },
  created_at: "2024-01-03T00:00:00Z",
  updated_at: "2024-01-03T00:00:00Z",
};
