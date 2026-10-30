import type {
  AuthProvider,
  AuthProviderDetails,
  OAuthProviderBrand,
  SamlSsoBinding,
  UpdateAuthProviderParams,
} from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { I18nKey } from "#/i18n/declaration";

/** Mirrors ``storage.auth_provider_details.AuthProviderDetails.kind`` for
 * the three kinds the form supports. */
export const KIND_OAUTH = "OAuthProviderDetails" as const;
export const KIND_PASSWORD = "PasswordProviderDetails" as const;
export const KIND_SAML = "SamlProviderDetails" as const;

/** Label for ``AuthProvider.kind`` (``type(details).__name__`` on the
 * backend) -- falls back to the raw value for a kind the UI doesn't have
 * copy for yet. */
export function kindLabel(t: (key: I18nKey) => string, kind: string): string {
  switch (kind) {
    case KIND_OAUTH:
      return t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_OAUTH);
    case KIND_PASSWORD:
      return t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD);
    case KIND_SAML:
      return t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML);
    default:
      return kind;
  }
}

/** Reuses the ``ProviderType`` enum values -- the built-in password IDP is
 * no longer one of these (it is its own top-level ``kind`` now). */
export const OAUTH_BRAND_OPTIONS: { key: OAuthProviderBrand; label: string }[] =
  [
    { key: "github", label: "GitHub" },
    { key: "gitlab", label: "GitLab" },
    { key: "bitbucket", label: "Bitbucket" },
    { key: "bitbucket_data_center", label: "Bitbucket Data Center" },
    { key: "forgejo", label: "Forgejo" },
    { key: "azure_devops", label: "Azure DevOps" },
    { key: "enterprise_sso", label: "Enterprise SSO" },
  ];

export type LegacyKeycloakType = Exclude<OAuthProviderBrand, "enterprise_sso">;

/** The ``named_secret`` a new provider of ``brand`` starts with -- the
 * ``<PROVIDER_TYPE>_TOKEN`` name git provider tokens have always used
 * (migration 198 backfills it). Login-only brands expose no token. */
export function defaultNamedSecret(brand: OAuthProviderBrand): string | null {
  return brand === "enterprise_sso" ? null : `${brand.toUpperCase()}_TOKEN`;
}

export const LEGACY_KEYCLOAK_TYPE_OPTIONS = OAUTH_BRAND_OPTIONS.filter(
  (option): option is { key: LegacyKeycloakType; label: string } =>
    option.key !== "enterprise_sso",
);

function parseScopes(value: string): string[] | null {
  const scopes = value
    .split(/[\s,]+/)
    .map((scope) => scope.trim())
    .filter(Boolean);
  return scopes.length > 0 ? scopes : null;
}

/** Parses the attribute-mapping field's ``openhands_field=provider_attr``
 * comma/newline-separated text into the OAuth/SAML ``attribute_mapping``
 * (keyed by ``openhands_field`` -- see ``server.auth.provider_identity
 * .map_oauth_claims`` and ``server.routes.saml._extract_mapped_attribute``).
 * Blank entries and entries missing a ``=`` are dropped. */
function parseAttributeMapping(value: string): Record<string, string> {
  const mapping: Record<string, string> = {};
  value
    .split(/[\n,]+/)
    .map((pair) => pair.trim())
    .filter(Boolean)
    .forEach((pair) => {
      const separatorIndex = pair.indexOf("=");
      if (separatorIndex <= 0) {
        return;
      }
      const key = pair.slice(0, separatorIndex).trim();
      const mappedTo = pair.slice(separatorIndex + 1).trim();
      if (key && mappedTo) {
        mapping[key] = mappedTo;
      }
    });
  return mapping;
}

/** Inverse of ``parseAttributeMapping``, for populating the form from an
 * existing provider. */
function formatAttributeMapping(mapping: Record<string, string>): string {
  return Object.entries(mapping)
    .map(([key, value]) => `${key}=${value}`)
    .join(", ");
}

const DEFAULT_PERMITTED_DRIFT_SECONDS = "60";
const DEFAULT_SSO_BINDING: SamlSsoBinding = "redirect";

export interface AuthProviderFormState {
  kind: typeof KIND_OAUTH | typeof KIND_PASSWORD | typeof KIND_SAML;
  title: string;
  isIdp: boolean;
  /** Raw SVG markup for this provider's login button -- row-level, not part
   * of ``details``, since every kind (OAuth/password/SAML) gets a button on
   * the "Let's Get Started" screen. Empty string means "unset" (``null`` on
   * the wire); the frontend falls back to a generic icon. */
  icon: string;
  /** CSS color string for this provider's login button -- defaults to
   * ``DEFAULT_AUTH_PROVIDER_COLOR`` (black), mirroring the backend. */
  color: string;
  /** Whether this provider may be used for a new login or link. Off by
   * default (mirroring the backend), so a new row stays inert until an
   * admin has finished configuring it. */
  enabled: boolean;
  /** Whether a brand-new user may be created the first time someone
   * authenticates through this provider, or only an already-linked account
   * may sign in through it. */
  allowRegistration: boolean;
  /** Initial value of a new user's ``User.allow_match_by_email`` flag when
   * they are first created by a login through this provider -- see
   * ``server.auth.provider_identity.resolve_or_create_user``. */
  allowMatchByEmail: boolean;
  providerType: OAuthProviderBrand;
  clientId: string;
  clientSecret: string;
  authorizationUrl: string;
  tokenUrl: string;
  userinfoUrl: string;
  scopes: string;
  permittedDriftSeconds: string;
  /** ``""`` when unset -- see ``OAuthProviderDetails.legacy_keycloak_type``. */
  legacyKeycloakType: LegacyKeycloakType | "";
  /** ``""`` when unset -- see ``OAuthProviderDetails.named_secret``. */
  namedSecret: string;
  metadataUrl: string;
  idpEntityId: string;
  ssoUrl: string;
  ssoBinding: SamlSsoBinding;
  sloUrl: string;
  idpX509Cert: string;
  /** Read-only: the SP's auto-generated, self-signed signing certificate,
   * for the admin to copy into the IdP's trusted-SP configuration (or to
   * hand out alongside the SP metadata URL). Empty until the row has been
   * saved at least once -- ``sp_private_key``/``sp_x509_cert`` are never
   * typed, only generated server-side as a pair (see
   * ``server.routes.oauth_providers_admin._ensure_sp_keypair``). */
  spX509Cert: string;
  nameIdFormat: string;
  /** ``openhands_field=provider_attr`` pairs, comma/newline-separated --
   * see ``parseAttributeMapping``. Shared by OAuth (``sub`` / ``email`` /
   * ``email_verified``) and SAML (``sub`` / ``email``). */
  attributeMapping: string;
}

const EMPTY_FORM: AuthProviderFormState = {
  kind: KIND_OAUTH,
  title: "",
  isIdp: true,
  icon: "",
  color: DEFAULT_AUTH_PROVIDER_COLOR,
  enabled: false,
  allowRegistration: true,
  allowMatchByEmail: true,
  providerType: "enterprise_sso",
  clientId: "",
  clientSecret: "",
  authorizationUrl: "",
  tokenUrl: "",
  userinfoUrl: "",
  scopes: "",
  permittedDriftSeconds: DEFAULT_PERMITTED_DRIFT_SECONDS,
  legacyKeycloakType: "",
  namedSecret: "",
  metadataUrl: "",
  idpEntityId: "",
  ssoUrl: "",
  ssoBinding: DEFAULT_SSO_BINDING,
  sloUrl: "",
  idpX509Cert: "",
  spX509Cert: "",
  nameIdFormat: "",
  attributeMapping: "",
};

export function toFormState(provider: AuthProvider): AuthProviderFormState {
  if (provider.details.kind === KIND_OAUTH) {
    const { details } = provider;
    return {
      ...EMPTY_FORM,
      kind: KIND_OAUTH,
      title: provider.title,
      isIdp: provider.is_idp,
      icon: provider.icon ?? "",
      color: provider.color,
      enabled: provider.enabled,
      allowRegistration: provider.allow_registration,
      allowMatchByEmail: provider.allow_match_by_email,
      providerType: details.provider_type,
      clientId: details.client_id,
      authorizationUrl: details.authorization_url ?? "",
      tokenUrl: details.token_url ?? "",
      userinfoUrl: details.userinfo_url ?? "",
      scopes: details.scopes?.join(" ") ?? "",
      permittedDriftSeconds: String(details.permitted_drift_seconds),
      legacyKeycloakType: details.legacy_keycloak_type ?? "",
      namedSecret: details.named_secret ?? "",
      attributeMapping: formatAttributeMapping(details.attribute_mapping),
    };
  }
  if (provider.details.kind === KIND_SAML) {
    const { details } = provider;
    return {
      ...EMPTY_FORM,
      kind: KIND_SAML,
      title: provider.title,
      isIdp: true,
      icon: provider.icon ?? "",
      color: provider.color,
      enabled: provider.enabled,
      allowRegistration: provider.allow_registration,
      allowMatchByEmail: provider.allow_match_by_email,
      metadataUrl: details.metadata_url ?? "",
      idpEntityId: details.idp_entity_id ?? "",
      ssoUrl: details.sso_url ?? "",
      ssoBinding: details.sso_binding,
      sloUrl: details.slo_url ?? "",
      idpX509Cert: details.idp_x509_cert ?? "",
      spX509Cert: details.sp_x509_cert ?? "",
      nameIdFormat: details.name_id_format ?? "",
      attributeMapping: formatAttributeMapping(details.attribute_mapping),
    };
  }
  return {
    ...EMPTY_FORM,
    kind: KIND_PASSWORD,
    title: provider.title,
    isIdp: true,
    icon: provider.icon ?? "",
    color: provider.color,
    enabled: provider.enabled,
    allowRegistration: provider.allow_registration,
    allowMatchByEmail: provider.allow_match_by_email,
  };
}

/** Whether every OAuth-specific field (everything except the secret, which
 * has its own clear-vs-keep handling) is unchanged from ``initial`` --
 * see ``buildUpdatePayload`` for why this matters. */
function oauthDetailsUnchanged(
  form: AuthProviderFormState,
  initial: AuthProviderFormState,
): boolean {
  return (
    form.providerType === initial.providerType &&
    form.clientId.trim() === initial.clientId.trim() &&
    form.authorizationUrl.trim() === initial.authorizationUrl.trim() &&
    form.tokenUrl.trim() === initial.tokenUrl.trim() &&
    form.userinfoUrl.trim() === initial.userinfoUrl.trim() &&
    form.scopes.trim() === initial.scopes.trim() &&
    form.permittedDriftSeconds.trim() ===
      initial.permittedDriftSeconds.trim() &&
    form.legacyKeycloakType === initial.legacyKeycloakType &&
    form.namedSecret.trim() === initial.namedSecret.trim() &&
    form.attributeMapping.trim() === initial.attributeMapping.trim()
  );
}

/** Whether every SAML-specific field (everything except the private key,
 * which has its own clear-vs-keep handling) is unchanged from ``initial``
 * -- see ``buildUpdatePayload`` for why this matters. */
function samlDetailsUnchanged(
  form: AuthProviderFormState,
  initial: AuthProviderFormState,
): boolean {
  return (
    form.metadataUrl.trim() === initial.metadataUrl.trim() &&
    form.idpEntityId.trim() === initial.idpEntityId.trim() &&
    form.ssoUrl.trim() === initial.ssoUrl.trim() &&
    form.ssoBinding === initial.ssoBinding &&
    form.sloUrl.trim() === initial.sloUrl.trim() &&
    form.idpX509Cert.trim() === initial.idpX509Cert.trim() &&
    form.nameIdFormat.trim() === initial.nameIdFormat.trim() &&
    form.attributeMapping.trim() === initial.attributeMapping.trim()
  );
}

function buildDetails(form: AuthProviderFormState): AuthProviderDetails {
  if (form.kind === KIND_PASSWORD) {
    return { kind: KIND_PASSWORD };
  }
  if (form.kind === KIND_SAML) {
    return {
      kind: KIND_SAML,
      metadata_url: form.metadataUrl.trim() || null,
      idp_entity_id: form.idpEntityId.trim() || null,
      sso_url: form.ssoUrl.trim() || null,
      sso_binding: form.ssoBinding,
      slo_url: form.sloUrl.trim() || null,
      idp_x509_cert: form.idpX509Cert.trim() || null,
      // Never admin-supplied -- auto-generated server-side the first time
      // this row becomes SAML, then carried forward on every later edit
      // (see ``server.routes.oauth_providers_admin._ensure_sp_keypair``).
      sp_private_key: null,
      // Generated server-side alongside ``sp_private_key`` -- never
      // client-supplied, and ignored by the server if sent (see type doc
      // comment).
      sp_x509_cert: null,
      name_id_format: form.nameIdFormat.trim() || null,
      attribute_mapping: parseAttributeMapping(form.attributeMapping),
    };
  }
  return {
    kind: KIND_OAUTH,
    provider_type: form.providerType,
    client_id: form.clientId.trim(),
    // Omitted (not ``null``) when blank: the server treats an explicit
    // ``null`` as "unset the secret" but keeps the existing one for an
    // omitted field (``_carry_forward_unset_oauth_fields``).
    ...(form.clientSecret && { client_secret: form.clientSecret }),
    authorization_url: form.authorizationUrl.trim() || null,
    token_url: form.tokenUrl.trim() || null,
    userinfo_url: form.userinfoUrl.trim() || null,
    scopes: parseScopes(form.scopes),
    permitted_drift_seconds: form.permittedDriftSeconds
      ? Number(form.permittedDriftSeconds)
      : Number(DEFAULT_PERMITTED_DRIFT_SECONDS),
    legacy_keycloak_type: form.legacyKeycloakType || null,
    named_secret: form.namedSecret.trim() || null,
    attribute_mapping: parseAttributeMapping(form.attributeMapping),
  };
}

/** ``details`` is replaced wholesale when present at all -- see
 * ``server.routes.oauth_providers_admin.UpdateAuthProviderRequest``. So,
 * for an edit, ``details`` is only sent if something about it actually
 * changed (the kind, an OAuth/SAML field, or a newly-typed secret); a pure
 * title/is_idp edit omits it entirely, which is what preserves the
 * existing (never-exposed) secret. */
export function buildUpdatePayload(
  form: AuthProviderFormState,
  initial: AuthProviderFormState,
): UpdateAuthProviderParams {
  const kindChanged = form.kind !== initial.kind;
  // SAML has no equivalent: the SP key is never admin-typed, so there is
  // nothing here that mirrors the OAuth client secret's "blank means keep,
  // non-blank means replace" contract.
  const secretTyped = form.kind === KIND_OAUTH && form.clientSecret !== "";
  const oauthFieldsChanged =
    form.kind === KIND_OAUTH && !oauthDetailsUnchanged(form, initial);
  const samlFieldsChanged =
    form.kind === KIND_SAML && !samlDetailsUnchanged(form, initial);
  const detailsChanged =
    kindChanged || secretTyped || oauthFieldsChanged || samlFieldsChanged;

  return {
    title: form.title.trim(),
    is_idp: form.isIdp,
    icon: form.icon.trim() || null,
    color: form.color.trim() || DEFAULT_AUTH_PROVIDER_COLOR,
    enabled: form.enabled,
    allow_registration: form.allowRegistration,
    allow_match_by_email: form.allowMatchByEmail,
    ...(detailsChanged && { details: buildDetails(form) }),
  };
}
