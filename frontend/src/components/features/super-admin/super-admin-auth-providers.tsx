import { useMemo, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { Plus } from "lucide-react";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { Toggle } from "#/components/shared/toggle/toggle";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import {
  useCreateAuthProvider,
  useDeleteAuthProvider,
  usePreviewSamlMetadata,
  useUpdateAuthProvider,
} from "#/hooks/mutation/use-auth-provider-mutations";
import { useAuthProviders } from "#/hooks/query/use-auth-providers";
import type {
  AuthProvider,
  AuthProviderDetails,
  CreateAuthProviderParams,
  OAuthProviderBrand,
  SamlIdpMetadata,
  SamlSsoBinding,
  UpdateAuthProviderParams,
} from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { downloadBlob } from "#/utils/utils";
import { I18nKey } from "#/i18n/declaration";
import {
  SUPER_ADMIN_NAV_ITEMS,
  SUPER_ADMIN_PATHS,
} from "#/constants/super-admin-nav";
import {
  BrandButton,
  SettingsInput,
  SettingsTextarea,
  SuperAdminPageHeader,
  SuperAdminRowMenu,
  SuperAdminTable,
} from "./super-admin-chrome";

/** A read-only URL field with a Copy button -- used for the per-row URLs
 * (OAuth redirect URL, SAML SP metadata URL) that only exist once a row has
 * been saved, and that an admin needs to copy into a third-party provider's
 * own configuration. */
function CopyableUrlField({
  testId,
  label,
  value,
  hint,
  hintTestId,
  copyTestId,
}: {
  testId: string;
  label: string;
  value: string;
  hint: string;
  hintTestId: string;
  copyTestId: string;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex items-end gap-2">
      <SettingsInput
        testId={testId}
        label={label}
        type="text"
        value={value}
        description={
          <p data-testid={hintTestId} className="text-xs text-tertiary-alt">
            {hint}
          </p>
        }
        isReadOnly
        onChange={() => {}}
        className="min-w-0 flex-1"
      />
      <BrandButton
        testId={copyTestId}
        type="button"
        variant="secondary"
        onClick={handleCopy}
      >
        {t(copied ? I18nKey.BUTTON$COPIED : I18nKey.BUTTON$COPY)}
      </BrandButton>
    </div>
  );
}

function navCopy(path: string) {
  return (
    SUPER_ADMIN_NAV_ITEMS.find((item) => item.to === path) ??
    SUPER_ADMIN_NAV_ITEMS[0]
  );
}

/** Mirrors ``storage.auth_provider_details.AuthProviderDetails.kind`` for
 * the three kinds the form supports. */
const KIND_OAUTH = "OAuthProviderDetails" as const;
const KIND_PASSWORD = "PasswordProviderDetails" as const;
const KIND_SAML = "SamlProviderDetails" as const;

/** Label for ``AuthProvider.kind`` (``type(details).__name__`` on the
 * backend) -- falls back to the raw value for a kind the UI doesn't have
 * copy for yet. */
function kindLabel(t: (key: I18nKey) => string, kind: string): string {
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
const OAUTH_BRAND_OPTIONS: { key: OAuthProviderBrand; label: string }[] = [
  { key: "github", label: "GitHub" },
  { key: "gitlab", label: "GitLab" },
  { key: "bitbucket", label: "Bitbucket" },
  { key: "bitbucket_data_center", label: "Bitbucket Data Center" },
  { key: "forgejo", label: "Forgejo" },
  { key: "azure_devops", label: "Azure DevOps" },
  { key: "enterprise_sso", label: "Enterprise SSO" },
];

type LegacyKeycloakType = Exclude<OAuthProviderBrand, "enterprise_sso">;

const LEGACY_KEYCLOAK_TYPE_OPTIONS = OAUTH_BRAND_OPTIONS.filter(
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

/** Parses the attribute-mapping field's ``openhands_field=saml_attr``
 * comma/newline-separated text into ``SamlProviderDetails.attribute_mapping``
 * (keyed by ``openhands_field`` -- see ``server.routes.saml
 * ._extract_mapped_attribute``, which looks up ``attribute_mapping[field]``
 * to get the SAML attribute name to read). Blank entries and entries
 * missing a ``=`` are dropped. */
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

interface AuthProviderFormState {
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
  /** ``openhands_field=saml_attr`` pairs, comma/newline-separated -- see
   * ``parseAttributeMapping``. Only the ``email`` key currently has any
   * effect server-side (``server.routes.saml._extract_mapped_attribute``
   * is only ever called for ``'email'``). */
  attributeMapping: string;
}

const EMPTY_FORM: AuthProviderFormState = {
  kind: KIND_OAUTH,
  title: "",
  isIdp: true,
  icon: "",
  color: DEFAULT_AUTH_PROVIDER_COLOR,
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

function toFormState(provider: AuthProvider): AuthProviderFormState {
  if (provider.details.kind === KIND_OAUTH) {
    const { details } = provider;
    return {
      ...EMPTY_FORM,
      kind: KIND_OAUTH,
      title: provider.title,
      isIdp: provider.is_idp,
      icon: provider.icon ?? "",
      color: provider.color,
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
    form.legacyKeycloakType === initial.legacyKeycloakType
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
  };
}

interface AuthProviderFormModalProps {
  mode: "create" | "edit";
  initial: AuthProviderFormState;
  /** The row's id -- only set in ``edit`` mode, once the row (and, for
   * SAML, its SP keypair) already exists server-side. Used to build the
   * per-row SP metadata URL (``server.routes.saml._sp_entity_id``), which
   * doesn't exist until then. */
  providerId?: number;
  isLoading: boolean;
  onClose: () => void;
  onSubmit: (
    form: AuthProviderFormState,
    initial: AuthProviderFormState,
  ) => void;
}

function AuthProviderFormModal({
  mode,
  initial,
  providerId,
  isLoading,
  onClose,
  onSubmit,
}: AuthProviderFormModalProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<AuthProviderFormState>(initial);
  const [metadataFetchError, setMetadataFetchError] = useState<string | null>(
    null,
  );
  const previewSamlMetadata = usePreviewSamlMetadata();
  // Guards against re-fetching on every blur (e.g. tabbing through the form
  // without touching this field again) -- only a URL not yet fetched
  // triggers a request.
  const lastFetchedMetadataUrl = useRef<string | null>(null);

  // Both URLs are built client-side rather than fetched, same origin as
  // this admin page (the OAuth/SAML routes are served by this same app).
  // Only shown once the row already exists server-side *as that kind* --
  // `initial` (not `form`) reflects what's actually persisted, so a kind
  // switch mid-edit (not yet saved) doesn't show a URL for a route that
  // doesn't 200 yet (`server.routes.oauth_v2`/`server.routes.saml` 404 a
  // mismatched kind).
  const oauthRedirectUrl =
    providerId !== undefined && initial.kind === KIND_OAUTH
      ? `${window.location.origin}/oauth/${providerId}/callback`
      : null;
  const spMetadataUrl =
    providerId !== undefined && initial.kind === KIND_SAML
      ? `${window.location.origin}/saml/${providerId}/metadata`
      : null;

  const handleDownloadSpCert = () => {
    if (!form.spX509Cert) {
      return;
    }
    downloadBlob(
      new Blob([form.spX509Cert], { type: "application/x-pem-file" }),
      `sp-certificate-${providerId ?? "new"}.pem`,
    );
  };

  const handleMetadataUrlBlur = () => {
    const metadataUrl = form.metadataUrl.trim();
    if (!metadataUrl || metadataUrl === lastFetchedMetadataUrl.current) {
      return;
    }
    lastFetchedMetadataUrl.current = metadataUrl;
    setMetadataFetchError(null);
    previewSamlMetadata.mutate(metadataUrl, {
      onSuccess: (data: SamlIdpMetadata) => {
        setForm((prev) => ({
          ...prev,
          idpEntityId: data.idp_entity_id ?? prev.idpEntityId,
          ssoUrl: data.sso_url ?? prev.ssoUrl,
          ssoBinding: data.sso_binding ?? prev.ssoBinding,
          sloUrl: data.slo_url ?? prev.sloUrl,
          idpX509Cert: data.idp_x509_cert ?? prev.idpX509Cert,
          nameIdFormat: data.name_id_format ?? prev.nameIdFormat,
        }));
      },
      onError: (error) => {
        // Let the admin retry the same URL (e.g. once their IdP's metadata
        // endpoint is live) instead of silently no-op'ing on the next blur.
        lastFetchedMetadataUrl.current = null;
        setMetadataFetchError(
          retrieveAxiosErrorMessage(error) ||
            t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_METADATA_FETCH_ERROR),
        );
      },
    });
  };

  const kindOptions = [
    {
      key: KIND_OAUTH,
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_OAUTH),
    },
    {
      key: KIND_PASSWORD,
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD),
    },
    {
      key: KIND_SAML,
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_SAML),
    },
  ];

  const ssoBindingOptions: { key: SamlSsoBinding; label: string }[] = [
    {
      key: "redirect",
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING_REDIRECT),
    },
    {
      key: "post",
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING_POST),
    },
  ];

  // SAML has no required field beyond the title: the IdP fields below are
  // all optional at creation time (see the hint rendered above them) --
  // plenty of IdPs won't hand over their entity ID/SSO URL/cert until
  // *after* they've been given this row's SP certificate and URLs, so the
  // row has to be creatable (to generate that cert) before any of them are
  // known.
  const canSubmit =
    form.title.trim() !== "" &&
    (form.kind === KIND_OAUTH ? form.clientId.trim() !== "" : true);

  return (
    <OrgModal
      testId="super-admin-auth-provider-form"
      title={t(
        mode === "create"
          ? I18nKey.SUPER_ADMIN$ADD_AUTH_PROVIDER
          : I18nKey.SUPER_ADMIN$EDIT_AUTH_PROVIDER,
      )}
      primaryButtonText={t(
        mode === "create" ? I18nKey.BUTTON$CREATE : I18nKey.BUTTON$SAVE,
      )}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      primaryButtonTestId="super-admin-auth-provider-form-submit"
      isPrimaryDisabled={!canSubmit}
      isLoading={isLoading}
      onPrimaryClick={() => onSubmit(form, initial)}
      onClose={onClose}
      className="w-md max-w-full"
    >
      <div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto pr-1">
        <SettingsDropdownInput
          testId="super-admin-auth-provider-kind"
          name="kind"
          label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND)}
          items={kindOptions}
          selectedKey={form.kind}
          onSelectionChange={(key) => {
            if (key) {
              setForm((prev) => ({
                ...prev,
                kind: key as
                  | typeof KIND_OAUTH
                  | typeof KIND_PASSWORD
                  | typeof KIND_SAML,
                isIdp: key === KIND_OAUTH ? prev.isIdp : true,
              }));
            }
          }}
          required
        />
        <SettingsInput
          testId="super-admin-auth-provider-title"
          label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_TITLE)}
          type="text"
          value={form.title}
          onChange={(value) => setForm((prev) => ({ ...prev, title: value }))}
          showRequiredTag
          autoComplete="off"
        />
        <SettingsInput
          testId="super-admin-auth-provider-color"
          label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_COLOR)}
          type="color"
          value={form.color || DEFAULT_AUTH_PROVIDER_COLOR}
          onChange={(value) => setForm((prev) => ({ ...prev, color: value }))}
          showOptionalTag
        />
        <SettingsTextarea
          testId="super-admin-auth-provider-icon"
          label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ICON)}
          value={form.icon}
          description={
            <p
              data-testid="super-admin-auth-provider-icon-hint"
              className="text-xs text-tertiary-alt"
            >
              {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ICON_HINT)}
            </p>
          }
          onChange={(value) => setForm((prev) => ({ ...prev, icon: value }))}
          rows={3}
          showOptionalTag
        />
        <SettingsSwitch
          testId="super-admin-auth-provider-allow-registration"
          isToggled={form.allowRegistration}
          onToggle={(value) =>
            setForm((prev) => ({ ...prev, allowRegistration: value }))
          }
        >
          {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ALLOW_REGISTRATION)}
        </SettingsSwitch>
        <SettingsSwitch
          testId="super-admin-auth-provider-allow-match-by-email"
          isToggled={form.allowMatchByEmail}
          onToggle={(value) =>
            setForm((prev) => ({ ...prev, allowMatchByEmail: value }))
          }
        >
          {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ALLOW_MATCH_BY_EMAIL)}
        </SettingsSwitch>
        {form.kind === KIND_OAUTH ? (
          <>
            <SettingsSwitch
              testId="super-admin-auth-provider-is-idp"
              isToggled={form.isIdp}
              onToggle={(value) =>
                setForm((prev) => ({ ...prev, isIdp: value }))
              }
            >
              {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_IS_IDP)}
            </SettingsSwitch>
            <SettingsDropdownInput
              testId="super-admin-auth-provider-brand"
              name="provider_type"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_BRAND)}
              items={OAUTH_BRAND_OPTIONS}
              selectedKey={form.providerType}
              onSelectionChange={(key) => {
                if (key) {
                  setForm((prev) => ({
                    ...prev,
                    providerType: key as OAuthProviderBrand,
                  }));
                }
              }}
              required
            />
            {oauthRedirectUrl ? (
              <CopyableUrlField
                testId="super-admin-auth-provider-redirect-url"
                label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_REDIRECT_URL)}
                value={oauthRedirectUrl}
                hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_REDIRECT_URL_HINT)}
                hintTestId="super-admin-auth-provider-redirect-url-hint"
                copyTestId="super-admin-auth-provider-redirect-url-copy"
              />
            ) : null}
            <SettingsInput
              testId="super-admin-auth-provider-client-id"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CLIENT_ID)}
              type="text"
              value={form.clientId}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, clientId: value }))
              }
              showRequiredTag
              autoComplete="off"
            />
            <SettingsInput
              testId="super-admin-auth-provider-client-secret"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CLIENT_SECRET)}
              type="password"
              value={form.clientSecret}
              placeholder={
                mode === "edit"
                  ? t(
                      I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CLIENT_SECRET_PLACEHOLDER,
                    )
                  : undefined
              }
              hint={
                mode === "edit"
                  ? t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CLIENT_SECRET_HINT)
                  : undefined
              }
              onChange={(value) =>
                setForm((prev) => ({ ...prev, clientSecret: value }))
              }
              showOptionalTag
              autoComplete="new-password"
            />
            <SettingsInput
              testId="super-admin-auth-provider-authorization-url"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_AUTH_URL)}
              type="text"
              value={form.authorizationUrl}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, authorizationUrl: value }))
              }
              showOptionalTag
            />
            <SettingsInput
              testId="super-admin-auth-provider-token-url"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_TOKEN_URL)}
              type="text"
              value={form.tokenUrl}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, tokenUrl: value }))
              }
              showOptionalTag
            />
            <SettingsInput
              testId="super-admin-auth-provider-userinfo-url"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USERINFO_URL)}
              type="text"
              value={form.userinfoUrl}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, userinfoUrl: value }))
              }
              showOptionalTag
            />
            <SettingsInput
              testId="super-admin-auth-provider-scopes"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SCOPES)}
              type="text"
              value={form.scopes}
              hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SCOPES_HINT)}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, scopes: value }))
              }
              showOptionalTag
            />
            <SettingsInput
              testId="super-admin-auth-provider-drift"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_DRIFT)}
              type="number"
              min={0}
              value={form.permittedDriftSeconds}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, permittedDriftSeconds: value }))
              }
              showOptionalTag
            />
            <SettingsDropdownInput
              testId="super-admin-auth-provider-legacy-keycloak-type"
              name="legacy_keycloak_type"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_LEGACY_KEYCLOAK_TYPE)}
              items={LEGACY_KEYCLOAK_TYPE_OPTIONS}
              selectedKey={form.legacyKeycloakType || null}
              onSelectionChange={(key) =>
                setForm((prev) => ({
                  ...prev,
                  legacyKeycloakType: (key ?? "") as LegacyKeycloakType | "",
                }))
              }
              isClearable
              showOptionalTag
            />
            <p
              data-testid="super-admin-auth-provider-legacy-keycloak-type-hint"
              className="text-xs text-tertiary-alt"
            >
              {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_LEGACY_KEYCLOAK_TYPE_HINT)}
            </p>
          </>
        ) : null}
        {form.kind === KIND_SAML ? (
          <>
            <p
              data-testid="super-admin-auth-provider-saml-fields-optional-hint"
              className="text-xs text-tertiary-alt"
            >
              {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SAML_FIELDS_OPTIONAL_HINT)}
            </p>
            <SettingsInput
              testId="super-admin-auth-provider-metadata-url"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_METADATA_URL)}
              type="text"
              value={form.metadataUrl}
              description={
                <p
                  data-testid="super-admin-auth-provider-metadata-url-hint"
                  className="text-xs text-tertiary-alt"
                >
                  {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_METADATA_URL_HINT)}
                </p>
              }
              onChange={(value) => {
                setForm((prev) => ({ ...prev, metadataUrl: value }));
                setMetadataFetchError(null);
              }}
              onBlur={handleMetadataUrlBlur}
              error={metadataFetchError ?? undefined}
              showOptionalTag
              autoComplete="off"
            />
            {previewSamlMetadata.isPending ? (
              <p
                data-testid="super-admin-auth-provider-metadata-url-loading"
                className="text-xs text-tertiary-alt"
              >
                {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_METADATA_FETCHING)}
              </p>
            ) : null}
            <SettingsInput
              testId="super-admin-auth-provider-idp-entity-id"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_IDP_ENTITY_ID)}
              type="text"
              value={form.idpEntityId}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, idpEntityId: value }))
              }
              showOptionalTag
              autoComplete="off"
            />
            <SettingsInput
              testId="super-admin-auth-provider-sso-url"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SSO_URL)}
              type="text"
              value={form.ssoUrl}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, ssoUrl: value }))
              }
              showOptionalTag
              autoComplete="off"
            />
            <SettingsDropdownInput
              testId="super-admin-auth-provider-sso-binding"
              name="sso_binding"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SSO_BINDING)}
              items={ssoBindingOptions}
              selectedKey={form.ssoBinding}
              onSelectionChange={(key) => {
                if (key) {
                  setForm((prev) => ({
                    ...prev,
                    ssoBinding: key as SamlSsoBinding,
                  }));
                }
              }}
              required
            />
            <SettingsInput
              testId="super-admin-auth-provider-slo-url"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SLO_URL)}
              type="text"
              value={form.sloUrl}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, sloUrl: value }))
              }
              showOptionalTag
              autoComplete="off"
            />
            <SettingsTextarea
              testId="super-admin-auth-provider-idp-x509-cert"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_IDP_X509_CERT)}
              value={form.idpX509Cert}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, idpX509Cert: value }))
              }
              rows={8}
              showOptionalTag
            />
            {spMetadataUrl ? (
              <CopyableUrlField
                testId="super-admin-auth-provider-sp-metadata-url"
                label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SP_METADATA_URL)}
                value={spMetadataUrl}
                hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SP_METADATA_URL_HINT)}
                hintTestId="super-admin-auth-provider-sp-metadata-url-hint"
                copyTestId="super-admin-auth-provider-sp-metadata-url-copy"
              />
            ) : null}
            {form.spX509Cert ? (
              <>
                <SettingsTextarea
                  testId="super-admin-auth-provider-sp-x509-cert"
                  label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SP_X509_CERT)}
                  value={form.spX509Cert}
                  description={
                    <p
                      data-testid="super-admin-auth-provider-sp-x509-cert-hint"
                      className="text-xs text-tertiary-alt"
                    >
                      {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SP_X509_CERT_HINT)}
                    </p>
                  }
                  rows={8}
                  isReadOnly
                  onChange={() => {}}
                />
                <BrandButton
                  testId="super-admin-auth-provider-sp-x509-cert-download"
                  type="button"
                  variant="secondary"
                  className="self-start"
                  onClick={handleDownloadSpCert}
                >
                  {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_DOWNLOAD_CERT_PEM)}
                </BrandButton>
              </>
            ) : (
              <p
                data-testid="super-admin-auth-provider-sp-x509-cert-pending"
                className="text-xs text-tertiary-alt"
              >
                {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_SP_X509_CERT_PENDING)}
              </p>
            )}
            <SettingsInput
              testId="super-admin-auth-provider-name-id-format"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_NAME_ID_FORMAT)}
              type="text"
              value={form.nameIdFormat}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, nameIdFormat: value }))
              }
              showOptionalTag
              autoComplete="off"
            />
            <SettingsTextarea
              testId="super-admin-auth-provider-attribute-mapping"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ATTRIBUTE_MAPPING)}
              value={form.attributeMapping}
              description={
                <p
                  data-testid="super-admin-auth-provider-attribute-mapping-hint"
                  className="text-xs text-tertiary-alt"
                >
                  {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ATTRIBUTE_MAPPING_HINT)}
                </p>
              }
              onChange={(value) =>
                setForm((prev) => ({ ...prev, attributeMapping: value }))
              }
              rows={3}
              showOptionalTag
            />
          </>
        ) : null}
      </div>
    </OrgModal>
  );
}

/** ``details`` is replaced wholesale when present at all -- see
 * ``server.routes.oauth_providers_admin.UpdateAuthProviderRequest``. So,
 * for an edit, ``details`` is only sent if something about it actually
 * changed (the kind, an OAuth/SAML field, or a newly-typed secret); a pure
 * title/is_idp edit omits it entirely, which is what preserves the
 * existing (never-exposed) secret. */
function buildUpdatePayload(
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
    allow_registration: form.allowRegistration,
    allow_match_by_email: form.allowMatchByEmail,
    ...(detailsChanged && { details: buildDetails(form) }),
  };
}

export function SuperAdminAuthProviders() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const copy = navCopy(SUPER_ADMIN_PATHS.authProviders);
  const { data, isLoading, isError } = useAuthProviders();
  const createProvider = useCreateAuthProvider();
  const updateProvider = useUpdateAuthProvider();
  const deleteProvider = useDeleteAuthProvider();

  const [formMode, setFormMode] = useState<"create" | "edit" | null>(null);
  const [editingProvider, setEditingProvider] = useState<AuthProvider | null>(
    null,
  );
  const [providerToDelete, setProviderToDelete] = useState<AuthProvider | null>(
    null,
  );

  const providers = useMemo(() => data ?? [], [data]);

  const openCreate = () => {
    setEditingProvider(null);
    setFormMode("create");
  };

  const openEdit = (provider: AuthProvider) => {
    setEditingProvider(provider);
    setFormMode("edit");
  };

  const closeForm = () => {
    setFormMode(null);
    setEditingProvider(null);
  };

  const handleSubmit = (
    form: AuthProviderFormState,
    initial: AuthProviderFormState,
  ) => {
    if (formMode === "edit" && editingProvider) {
      updateProvider.mutate(
        { id: editingProvider.id, ...buildUpdatePayload(form, initial) },
        { onSuccess: closeForm },
      );
    } else {
      const payload: CreateAuthProviderParams = {
        title: form.title.trim(),
        is_idp: form.isIdp,
        icon: form.icon.trim() || null,
        color: form.color.trim() || DEFAULT_AUTH_PROVIDER_COLOR,
        allow_registration: form.allowRegistration,
        allow_match_by_email: form.allowMatchByEmail,
        details: buildDetails(form),
      };
      createProvider.mutate(payload, { onSuccess: closeForm });
    }
  };

  const confirmDelete = () => {
    if (!providerToDelete) {
      return;
    }
    deleteProvider.mutate(
      { id: providerToDelete.id },
      { onSuccess: () => setProviderToDelete(null) },
    );
  };

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-auth-providers"
    >
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          <BrandButton
            type="button"
            variant="primary"
            testId="super-admin-add-auth-provider"
            startContent={<Plus className="h-4 w-4" />}
            onClick={openCreate}
          >
            {t(I18nKey.SUPER_ADMIN$ADD_AUTH_PROVIDER)}
          </BrandButton>
        }
      />
      {isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {isError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      <SuperAdminTable<AuthProvider>
        testId="super-admin-auth-providers-table"
        rows={providers}
        getRowKey={(row) => String(row.id)}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_AUTH_PROVIDERS)}
        columns={[
          {
            key: "color",
            header: "",
            className: "w-12 min-w-12",
            render: (row) => (
              <span
                data-testid={`super-admin-auth-provider-color-swatch-${row.id}`}
                role="img"
                aria-label={`${t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_COLOR)}: ${row.color}`}
                title={row.color}
                className="block size-5 rounded-sm border border-white/20"
                style={{ backgroundColor: row.color }}
              />
            ),
          },
          {
            key: "title",
            header: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_TITLE),
            className: "w-[50%]",
            render: (row) => (
              <span className="block truncate">{row.title}</span>
            ),
          },
          {
            key: "type",
            header: t(I18nKey.SUPER_ADMIN$COL_TYPE),
            className: "w-[20%]",
            render: (row) => kindLabel(t, row.kind),
          },
          {
            key: "is_idp",
            header: t(I18nKey.SUPER_ADMIN$COL_IS_IDP),
            className: "w-[8%]",
            // Password and SAML providers are always login IDPs (see
            // ``toFormState``), so only an OAuth row can be switched here.
            render: (row) => (
              <Toggle
                checked={row.is_idp}
                disabled={row.kind !== KIND_OAUTH || updateProvider.isPending}
                onClick={() =>
                  updateProvider.mutate({ id: row.id, is_idp: !row.is_idp })
                }
                aria-label={`${t(I18nKey.SUPER_ADMIN$COL_IS_IDP)}: ${row.title}`}
              />
            ),
          },
          {
            key: "actions",
            header: "",
            className: "w-12 min-w-12 text-right",
            render: (row) => (
              <SuperAdminRowMenu
                testId={`super-admin-auth-provider-actions-${row.id}`}
                ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                items={[
                  {
                    label: t(I18nKey.BUTTON$EDIT),
                    testId: `super-admin-auth-provider-edit-${row.id}`,
                    onSelect: () => openEdit(row),
                  },
                  {
                    label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_USER_LINKS),
                    testId: `super-admin-auth-provider-user-links-${row.id}`,
                    onSelect: () =>
                      navigate(
                        `${SUPER_ADMIN_PATHS.authProviders}/${row.id}/users`,
                      ),
                  },
                  {
                    label: t(I18nKey.BUTTON$DELETE),
                    testId: `super-admin-auth-provider-delete-${row.id}`,
                    destructive: true,
                    onSelect: () => setProviderToDelete(row),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      {formMode ? (
        <AuthProviderFormModal
          mode={formMode}
          initial={editingProvider ? toFormState(editingProvider) : EMPTY_FORM}
          // Only set once this row already exists server-side -- the
          // OAuth redirect/SAML SP metadata URLs don't exist for a row
          // that's still being created (see the per-kind gating above
          // `oauthRedirectUrl`/`spMetadataUrl`).
          providerId={editingProvider?.id}
          isLoading={createProvider.isPending || updateProvider.isPending}
          onClose={closeForm}
          onSubmit={handleSubmit}
        />
      ) : null}
      {providerToDelete ? (
        <OrgModal
          testId="super-admin-auth-provider-delete-confirm"
          title={t(I18nKey.SUPER_ADMIN$DELETE_AUTH_PROVIDER_TITLE)}
          description={
            <Trans
              i18nKey={I18nKey.SUPER_ADMIN$DELETE_AUTH_PROVIDER_CONFIRM}
              values={{ name: providerToDelete.title }}
              components={{ name: <span className="text-white" /> }}
            />
          }
          primaryButtonText={t(I18nKey.BUTTON$CONFIRM)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          primaryButtonTestId="super-admin-auth-provider-delete-confirm-submit"
          secondaryButtonTestId="super-admin-auth-provider-delete-confirm-cancel"
          onPrimaryClick={confirmDelete}
          onClose={() => setProviderToDelete(null)}
          isLoading={deleteProvider.isPending}
        />
      ) : null}
    </div>
  );
}
