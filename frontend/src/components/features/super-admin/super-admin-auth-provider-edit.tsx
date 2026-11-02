import { type FormEvent, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { FaChevronLeft } from "react-icons/fa6";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import {
  usePreviewSamlMetadata,
  useUpdateAuthProvider,
} from "#/hooks/mutation/use-auth-provider-mutations";
import { useAuthProvider } from "#/hooks/query/use-auth-providers";
import type {
  AuthProvider,
  OAuthProviderBrand,
  SamlIdpMetadata,
  SamlSsoBinding,
} from "#/api/auth-providers-service/auth-providers.types";
import { DEFAULT_AUTH_PROVIDER_COLOR } from "#/api/auth-providers-service/auth-providers.types";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { cn, downloadBlob } from "#/utils/utils";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import {
  BrandButton,
  SettingsInput,
  SettingsTextarea,
  SuperAdminPageHeader,
} from "./super-admin-chrome";
import {
  type AuthProviderFormState,
  KIND_OAUTH,
  KIND_PASSWORD,
  KIND_SAML,
  LEGACY_KEYCLOAK_TYPE_OPTIONS,
  type LegacyKeycloakType,
  OAUTH_BRAND_OPTIONS,
  buildUpdatePayload,
  toFormState,
} from "./auth-provider-form";

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

interface AuthProviderFormProps {
  provider: AuthProvider;
}

/** Keyed on the persisted row by the caller, so a successful save (which
 * refetches the row) resets the form to what the server now holds. */
function AuthProviderForm({ provider }: AuthProviderFormProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const updateProvider = useUpdateAuthProvider();
  const providerId = provider.id;
  const initial = useMemo(() => toFormState(provider), [provider]);
  const hasClientSecret =
    provider.details.kind === KIND_OAUTH && !!provider.details.client_secret;
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
  // Keyed on `initial` (not `form`), which reflects what's actually
  // persisted, so a kind switch mid-edit (not yet saved) doesn't show a URL
  // for a route that doesn't 200 yet (`server.routes.oauth_v2`/
  // `server.routes.saml` 404 a mismatched kind).
  const oauthRedirectUrl =
    initial.kind === KIND_OAUTH
      ? `${window.location.origin}/oauth/${providerId}/callback`
      : null;
  const spMetadataUrl =
    initial.kind === KIND_SAML
      ? `${window.location.origin}/saml/${providerId}/metadata`
      : null;

  const handleDownloadSpCert = () => {
    if (!form.spX509Cert) {
      return;
    }
    downloadBlob(
      new Blob([form.spX509Cert], { type: "application/x-pem-file" }),
      `sp-certificate-${providerId}.pem`,
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

  // SAML has no required field beyond the title: plenty of IdPs won't hand
  // over their entity ID/SSO URL/cert until *after* they've been given this
  // row's SP certificate and URLs (see the hint rendered above them).
  const canSubmit =
    form.title.trim() !== "" &&
    (form.kind === KIND_OAUTH ? form.clientId.trim() !== "" : true);
  const isDirty = JSON.stringify(form) !== JSON.stringify(initial);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    updateProvider.mutate(
      {
        id: providerId,
        ...buildUpdatePayload(form, initial),
      },
      { onSuccess: () => navigate(SUPER_ADMIN_PATHS.authProviders) },
    );
  };

  return (
    <form
      data-testid="super-admin-auth-provider-form"
      className="flex flex-col gap-6"
      onSubmit={handleSubmit}
    >
      <div className="flex max-w-2xl flex-col gap-4">
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
          testId="super-admin-auth-provider-enabled"
          isToggled={form.enabled}
          onToggle={(value) => setForm((prev) => ({ ...prev, enabled: value }))}
        >
          {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ENABLED)}
        </SettingsSwitch>
        <SettingsSwitch
          testId="super-admin-auth-provider-allow-registration"
          isToggled={form.allowRegistration}
          onToggle={(value) =>
            setForm((prev) => ({ ...prev, allowRegistration: value }))
          }
          hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ALLOW_REGISTRATION_HINT)}
        >
          {t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ALLOW_REGISTRATION)}
        </SettingsSwitch>
        <SettingsSwitch
          testId="super-admin-auth-provider-allow-match-by-email"
          isToggled={form.allowMatchByEmail}
          onToggle={(value) =>
            setForm((prev) => ({ ...prev, allowMatchByEmail: value }))
          }
          hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ALLOW_MATCH_BY_EMAIL_HINT)}
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
              hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_IS_IDP_HINT)}
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
                hasClientSecret
                  ? t(
                      I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CLIENT_SECRET_PLACEHOLDER,
                    )
                  : undefined
              }
              hint={
                hasClientSecret
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
            <SettingsInput
              testId="super-admin-auth-provider-named-secret"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_NAMED_SECRET)}
              type="text"
              value={form.namedSecret}
              hint={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_NAMED_SECRET_HINT)}
              onChange={(value) =>
                setForm((prev) => ({ ...prev, namedSecret: value }))
              }
              showOptionalTag
              autoComplete="off"
            />
            <SettingsTextarea
              testId="super-admin-auth-provider-oauth-attribute-mapping"
              label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_ATTRIBUTE_MAPPING)}
              value={form.attributeMapping}
              description={
                <p
                  data-testid="super-admin-auth-provider-oauth-attribute-mapping-hint"
                  className="text-xs text-tertiary-alt"
                >
                  {t(
                    I18nKey.SUPER_ADMIN$AUTH_PROVIDER_OAUTH_ATTRIBUTE_MAPPING_HINT,
                  )}
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
      <BrandButton
        testId="super-admin-auth-provider-form-submit"
        type="submit"
        variant="primary"
        className="self-start"
        isDisabled={!canSubmit || !isDirty || updateProvider.isPending}
      >
        {t(I18nKey.SETTINGS$SAVE_CHANGES)}
      </BrandButton>
    </form>
  );
}

/** Super Admin page for configuring a single auth provider --
 * ``/super-admin/auth-providers/{provider_id}``. */
export function SuperAdminAuthProviderEdit() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { providerId } = useParams<{ providerId: string }>();
  const {
    data: provider,
    isLoading,
    isError,
  } = useAuthProvider(Number(providerId));

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-auth-provider-edit"
    >
      <button
        type="button"
        data-testid="super-admin-auth-provider-edit-back"
        onClick={() => navigate(SUPER_ADMIN_PATHS.authProviders)}
        className={cn(
          "inline-flex w-fit shrink-0 items-center gap-1.5",
          "text-xs font-medium text-[var(--oh-muted)] hover:text-white",
          "transition-colors",
        )}
      >
        <FaChevronLeft size={10} aria-hidden="true" />
        {t(I18nKey.SUPER_ADMIN$BACK_TO_AUTH_PROVIDERS)}
      </button>
      <SuperAdminPageHeader
        title={t(I18nKey.SUPER_ADMIN$EDIT_AUTH_PROVIDER)}
        subtitle={provider?.title ?? ""}
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
      {provider ? (
        <AuthProviderForm key={provider.updated_at} provider={provider} />
      ) : null}
    </div>
  );
}
