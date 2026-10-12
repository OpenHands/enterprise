import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { Toggle } from "#/components/shared/toggle/toggle";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import {
  useCreateAuthProvider,
  useDeleteAuthProvider,
  useUpdateAuthProvider,
} from "#/hooks/mutation/use-auth-provider-mutations";
import { useAuthProviders } from "#/hooks/query/use-auth-providers";
import type {
  AuthProvider,
  AuthProviderDetails,
  CreateAuthProviderParams,
  OAuthProviderBrand,
  UpdateAuthProviderParams,
} from "#/api/auth-providers-service/auth-providers.types";
import { I18nKey } from "#/i18n/declaration";
import {
  SUPER_ADMIN_NAV_ITEMS,
  SUPER_ADMIN_PATHS,
} from "#/constants/super-admin-nav";
import {
  BrandButton,
  SettingsInput,
  SuperAdminPageHeader,
  SuperAdminRowMenu,
  SuperAdminTable,
} from "./super-admin-chrome";

function navCopy(path: string) {
  return (
    SUPER_ADMIN_NAV_ITEMS.find((item) => item.to === path) ??
    SUPER_ADMIN_NAV_ITEMS[0]
  );
}

/** Mirrors ``storage.auth_provider_details.AuthProviderDetails.kind`` for
 * the two kinds the form supports today -- SAML is a follow-up. */
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

function parseScopes(value: string): string[] | null {
  const scopes = value
    .split(/[\s,]+/)
    .map((scope) => scope.trim())
    .filter(Boolean);
  return scopes.length > 0 ? scopes : null;
}

const DEFAULT_PERMITTED_DRIFT_SECONDS = "60";

interface AuthProviderFormState {
  kind: typeof KIND_OAUTH | typeof KIND_PASSWORD;
  title: string;
  isIdp: boolean;
  providerType: OAuthProviderBrand;
  clientId: string;
  clientSecret: string;
  authorizationUrl: string;
  tokenUrl: string;
  userinfoUrl: string;
  scopes: string;
  permittedDriftSeconds: string;
}

const EMPTY_FORM: AuthProviderFormState = {
  kind: KIND_OAUTH,
  title: "",
  isIdp: true,
  providerType: "enterprise_sso",
  clientId: "",
  clientSecret: "",
  authorizationUrl: "",
  tokenUrl: "",
  userinfoUrl: "",
  scopes: "",
  permittedDriftSeconds: DEFAULT_PERMITTED_DRIFT_SECONDS,
};

function toFormState(provider: AuthProvider): AuthProviderFormState {
  if (provider.details.kind === KIND_OAUTH) {
    const { details } = provider;
    return {
      kind: KIND_OAUTH,
      title: provider.title,
      isIdp: provider.is_idp,
      providerType: details.provider_type,
      clientId: details.client_id,
      clientSecret: "",
      authorizationUrl: details.authorization_url ?? "",
      tokenUrl: details.token_url ?? "",
      userinfoUrl: details.userinfo_url ?? "",
      scopes: details.scopes?.join(" ") ?? "",
      permittedDriftSeconds: String(details.permitted_drift_seconds),
    };
  }
  return {
    ...EMPTY_FORM,
    kind: KIND_PASSWORD,
    title: provider.title,
    isIdp: true,
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
    form.permittedDriftSeconds.trim() === initial.permittedDriftSeconds.trim()
  );
}

function buildDetails(form: AuthProviderFormState): AuthProviderDetails {
  if (form.kind === KIND_PASSWORD) {
    return { kind: KIND_PASSWORD };
  }
  return {
    kind: KIND_OAUTH,
    provider_type: form.providerType,
    client_id: form.clientId.trim(),
    client_secret: form.clientSecret ? form.clientSecret : null,
    authorization_url: form.authorizationUrl.trim() || null,
    token_url: form.tokenUrl.trim() || null,
    userinfo_url: form.userinfoUrl.trim() || null,
    scopes: parseScopes(form.scopes),
    permitted_drift_seconds: form.permittedDriftSeconds
      ? Number(form.permittedDriftSeconds)
      : Number(DEFAULT_PERMITTED_DRIFT_SECONDS),
  };
}

interface AuthProviderFormModalProps {
  mode: "create" | "edit";
  initial: AuthProviderFormState;
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
  isLoading,
  onClose,
  onSubmit,
}: AuthProviderFormModalProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<AuthProviderFormState>(initial);

  const kindOptions = [
    {
      key: KIND_OAUTH,
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_OAUTH),
    },
    {
      key: KIND_PASSWORD,
      label: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_KIND_PASSWORD),
    },
  ];

  const canSubmit =
    form.title.trim() !== "" &&
    (form.kind === KIND_PASSWORD || form.clientId.trim() !== "");

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
                kind: key as typeof KIND_OAUTH | typeof KIND_PASSWORD,
                isIdp: key === KIND_PASSWORD ? true : prev.isIdp,
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
          </>
        ) : null}
      </div>
    </OrgModal>
  );
}

/** ``details`` is replaced wholesale when present at all -- see
 * ``server.routes.oauth_providers_admin.UpdateAuthProviderRequest``. So,
 * for an edit, ``details`` is only sent if something about it actually
 * changed (the kind, an OAuth field, or a newly-typed secret); a pure
 * title/is_idp edit omits it entirely, which is what preserves the
 * existing (never-exposed) secret. */
function buildUpdatePayload(
  form: AuthProviderFormState,
  initial: AuthProviderFormState,
): UpdateAuthProviderParams {
  const kindChanged = form.kind !== initial.kind;
  const secretTyped = form.kind === KIND_OAUTH && form.clientSecret !== "";
  const oauthFieldsChanged =
    form.kind === KIND_OAUTH && !oauthDetailsUnchanged(form, initial);
  const detailsChanged = kindChanged || secretTyped || oauthFieldsChanged;

  return {
    title: form.title.trim(),
    is_idp: form.isIdp,
    ...(detailsChanged && { details: buildDetails(form) }),
  };
}

export function SuperAdminAuthProviders() {
  const { t } = useTranslation();
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
            key: "title",
            header: t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_TITLE),
            className: "w-[60%]",
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
            render: (row) => (
              <Toggle
                checked={row.is_idp}
                disabled
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
