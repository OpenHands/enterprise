import githubLogo from "#/assets/branding/github-logo.svg?raw";
import gitlabLogo from "#/assets/branding/gitlab-logo.svg?raw";
import bitbucketLogo from "#/assets/branding/bitbucket-logo.svg?raw";
import azureDevOpsLogo from "#/assets/branding/azure-devops-logo.svg?raw";
import type {
  CreateAuthProviderParams,
  OAuthProviderDetails,
} from "#/api/auth-providers-service/auth-providers.types";
import { I18nKey } from "#/i18n/declaration";
import { defaultNamedSecret } from "./auth-provider-form";

export type AuthProviderPresetKey =
  | "google"
  | "okta"
  | "oauth"
  | "github"
  | "gitlab"
  | "bitbucket"
  | "azure_devops"
  | "saml"
  | "password";

export interface AuthProviderPreset {
  key: AuthProviderPresetKey;
  label: I18nKey;
  params: CreateAuthProviderParams;
}

const PLACEHOLDER_CLIENT_ID = "PLACEHOLDER";

// The login screen renders `icon` as a standalone `data:image/svg+xml` <img>,
// where `currentColor` would resolve to black rather than the button's text
// color -- same reason migration 189 bakes white into its seeded icons.
const whiteIcon = (svg: string) =>
  svg.replaceAll("currentColor", "#FFFFFF").replace(/>\s+</g, "><").trim();

function oauthDetails(
  details: Omit<
    OAuthProviderDetails,
    | "kind"
    | "client_id"
    | "permitted_drift_seconds"
    | "legacy_keycloak_type"
    | "named_secret"
  >,
): OAuthProviderDetails {
  return {
    kind: "OAuthProviderDetails",
    client_id: PLACEHOLDER_CLIENT_ID,
    permitted_drift_seconds: 60,
    legacy_keycloak_type: null,
    named_secret: defaultNamedSecret(details.provider_type),
    ...details,
  };
}

const LOGIN_DEFAULTS = {
  is_idp: true,
  icon: null,
  // Every preset seeds placeholder URLs or is otherwise not ready to serve
  // logins, so rows are created switched off and the admin enables the one
  // they have finished configuring.
  enabled: false,
  allow_registration: true,
  allow_match_by_email: true,
} as const;

/** Starting points for the "Add Provider" modal. Each creates a row with
 * placeholder values so the admin can get its callback/metadata URL (which
 * needs a row id) before the external IdP will hand over its own details.
 * The git-provider presets mirror the endpoints/scopes migration 168 seeds
 * and the titles/colors/icons migration 189 seeds. */
export const AUTH_PROVIDER_PRESETS: AuthProviderPreset[] = [
  {
    key: "google",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_GOOGLE,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Log in with Google",
      color: "#1a73e8",
      details: oauthDetails({
        provider_type: "enterprise_sso",
        authorization_url: "https://accounts.google.com/o/oauth2/auth",
        token_url: "https://oauth2.googleapis.com/token",
        userinfo_url: "https://openidconnect.googleapis.com/v1/userinfo",
        // No offline_access: Google rejects it and uses access_type=offline.
        scopes: ["openid", "email"],
      }),
    },
  },
  {
    key: "okta",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_OKTA,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Login with Okta",
      color: "#4c64e1",
      details: oauthDetails({
        provider_type: "enterprise_sso",
        authorization_url: "https://YOUR_DOMAIN.okta.com/oauth2/v1/authorize",
        token_url: "https://YOUR_DOMAIN.okta.com/oauth2/v1/token",
        userinfo_url: "https://YOUR_DOMAIN.okta.com/oauth2/v1/userinfo",
        scopes: ["openid", "email", "offline_access"],
      }),
    },
  },
  {
    key: "oauth",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_OAUTH,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Login with OAuth",
      color: "#4c64e1",
      details: oauthDetails({
        provider_type: "enterprise_sso",
        authorization_url: "https://PLACEHOLDER.COM/OAUTH/auth",
        token_url: "https://PLACEHOLDER.COM/OAUTH/token",
        userinfo_url: "https://PLACEHOLDER.COM/OAUTH/userinfo",
        scopes: ["openid", "email", "offline_access"],
      }),
    },
  },
  {
    key: "github",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITHUB,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Log in with GitHub",
      icon: whiteIcon(githubLogo),
      color: "#9E28B0",
      details: oauthDetails({
        provider_type: "github",
        authorization_url: "https://github.com/login/oauth/authorize",
        token_url: "https://github.com/login/oauth/access_token",
        userinfo_url: "https://api.github.com/user",
        scopes: ["repo", "user:email", "workflow"],
      }),
    },
  },
  {
    key: "gitlab",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_GITLAB,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Log in with GitLab",
      icon: whiteIcon(gitlabLogo),
      color: "#FC6B0E",
      details: oauthDetails({
        provider_type: "gitlab",
        authorization_url: "https://gitlab.com/oauth/authorize",
        token_url: "https://gitlab.com/oauth/token",
        userinfo_url: "https://gitlab.com/api/v4/user",
        scopes: ["api", "read_user", "write_repository"],
      }),
    },
  },
  {
    key: "bitbucket",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_BITBUCKET,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Log in with Bitbucket",
      icon: whiteIcon(bitbucketLogo),
      color: "#2684FF",
      details: oauthDetails({
        provider_type: "bitbucket",
        authorization_url: "https://bitbucket.org/site/oauth2/authorize",
        token_url: "https://bitbucket.org/site/oauth2/access_token",
        userinfo_url: "https://api.bitbucket.org/2.0/user",
        scopes: ["repository", "account"],
      }),
    },
  },
  {
    key: "azure_devops",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_AZURE_DEVOPS,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Log in with Azure DevOps",
      icon: whiteIcon(azureDevOpsLogo),
      color: "#0078D4",
      details: oauthDetails({
        provider_type: "azure_devops",
        authorization_url:
          "https://login.microsoftonline.com/YOUR_TENANT_ID/oauth2/v2.0/authorize",
        token_url:
          "https://login.microsoftonline.com/YOUR_TENANT_ID/oauth2/v2.0/token",
        // No userinfo endpoint: Entra's is on Graph and rejects an Azure
        // DevOps access token, so login reads the user from the ID token.
        userinfo_url: null,
        // 499b84ac-... is Azure DevOps' resource ID in Entra. Named scopes,
        // unlike .default, need no API permission on the app registration.
        scopes: [
          "openid",
          "email",
          "profile",
          "offline_access",
          "499b84ac-1321-427f-aa17-267ca6975798/vso.profile",
          "499b84ac-1321-427f-aa17-267ca6975798/vso.code_write",
          "499b84ac-1321-427f-aa17-267ca6975798/vso.project",
          "499b84ac-1321-427f-aa17-267ca6975798/vso.work_write",
        ],
      }),
    },
  },
  {
    key: "saml",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_PRESET_SAML,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Login with SAML",
      color: "#4c64e1",
      details: {
        kind: "SamlProviderDetails",
        // Left unset: the edit page prefills the IdP fields from this URL
        // once a real one is entered.
        metadata_url: null,
        idp_entity_id: "https://PLACEHOLDER.COM/SAML/entity",
        sso_url: "https://PLACEHOLDER.COM/SAML/sso",
        sso_binding: "redirect",
        slo_url: null,
        idp_x509_cert: null,
        sp_private_key: null,
        sp_x509_cert: null,
        // An email-typed NameID is used as the user's email when no
        // attribute mapping is configured (see `server.routes.saml`).
        name_id_format:
          "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        attribute_mapping: {},
      },
    },
  },
  {
    key: "password",
    label: I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD,
    params: {
      ...LOGIN_DEFAULTS,
      title: "Login with Password",
      color: "#03A956",
      details: { kind: "PasswordProviderDetails" },
    },
  },
];
