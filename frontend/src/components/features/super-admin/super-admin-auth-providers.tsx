import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import {
  useCreateOAuthProvider,
  useDeleteOAuthProvider,
  useUpdateOAuthProvider,
} from "#/hooks/mutation/use-oauth-provider-mutations";
import { useOAuthProviders } from "#/hooks/query/use-oauth-providers";
import type { OAuthProvider } from "#/api/oauth-providers-service/oauth-providers.types";
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

/** Reuses the ProviderType enum values plus the built-in password IDP. */
const PROVIDER_CATEGORY_OPTIONS = [
  { key: "github", label: "GitHub" },
  { key: "gitlab", label: "GitLab" },
  { key: "bitbucket", label: "Bitbucket" },
  { key: "bitbucket_data_center", label: "Bitbucket Data Center" },
  { key: "forgejo", label: "Forgejo" },
  { key: "azure_devops", label: "Azure DevOps" },
  { key: "enterprise_sso", label: "Enterprise SSO" },
  { key: "integrated_idp", label: "Integrated (password) IDP" },
];

function parseScopes(value: string): string[] | null {
  const scopes = value
    .split(/[\s,]+/)
    .map((scope) => scope.trim())
    .filter(Boolean);
  return scopes.length > 0 ? scopes : null;
}

interface OAuthProviderFormState {
  providerCategory: string;
  displayName: string;
  isIdp: boolean;
  clientId: string;
  clientSecret: string;
  authorizationUrl: string;
  tokenUrl: string;
  userinfoUrl: string;
  scopes: string;
  permittedDriftSeconds: string;
}

const EMPTY_FORM: OAuthProviderFormState = {
  providerCategory: "enterprise_sso",
  displayName: "",
  isIdp: true,
  clientId: "",
  clientSecret: "",
  authorizationUrl: "",
  tokenUrl: "",
  userinfoUrl: "",
  scopes: "",
  permittedDriftSeconds: "60",
};

function toFormState(provider: OAuthProvider): OAuthProviderFormState {
  return {
    providerCategory: provider.provider_category,
    displayName: provider.display_name,
    isIdp: provider.is_idp,
    clientId: provider.client_id,
    clientSecret: "",
    authorizationUrl: provider.authorization_url ?? "",
    tokenUrl: provider.token_url ?? "",
    userinfoUrl: provider.userinfo_url ?? "",
    scopes: provider.scopes?.join(" ") ?? "",
    permittedDriftSeconds: String(provider.permitted_drift_seconds),
  };
}

interface OAuthProviderFormModalProps {
  mode: "create" | "edit";
  initial: OAuthProviderFormState;
  isLoading: boolean;
  onClose: () => void;
  onSubmit: (form: OAuthProviderFormState) => void;
}

function OAuthProviderFormModal({
  mode,
  initial,
  isLoading,
  onClose,
  onSubmit,
}: OAuthProviderFormModalProps) {
  const { t } = useTranslation();
  const [form, setForm] = useState<OAuthProviderFormState>(initial);

  const canSubmit =
    form.providerCategory.trim() !== "" &&
    form.displayName.trim() !== "" &&
    form.clientId.trim() !== "";

  return (
    <OrgModal
      testId="super-admin-oauth-provider-form"
      title={t(
        mode === "create"
          ? I18nKey.SUPER_ADMIN$ADD_OAUTH_PROVIDER
          : I18nKey.SUPER_ADMIN$EDIT_OAUTH_PROVIDER,
      )}
      primaryButtonText={t(
        mode === "create" ? I18nKey.BUTTON$CREATE : I18nKey.BUTTON$SAVE,
      )}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      primaryButtonTestId="super-admin-oauth-provider-form-submit"
      isPrimaryDisabled={!canSubmit}
      isLoading={isLoading}
      onPrimaryClick={() => onSubmit(form)}
      onClose={onClose}
      className="w-md max-w-full"
    >
      <div className="flex max-h-[60vh] flex-col gap-4 overflow-y-auto pr-1">
        <SettingsDropdownInput
          testId="super-admin-oauth-provider-category"
          name="provider_category"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CATEGORY)}
          items={PROVIDER_CATEGORY_OPTIONS}
          defaultSelectedKey={form.providerCategory || undefined}
          allowsCustomValue
          onSelectionChange={(key) => {
            // The underlying Autocomplete also fires `onSelectionChange(null)`
            // on blur once its displayed text no longer matches the selected
            // item's label (see `onInputChange` below) -- that is an internal
            // deselect, not the user clearing the field (there's no clear
            // button here), so it must not wipe out a value we already set.
            if (key) {
              setForm((prev) => ({ ...prev, providerCategory: String(key) }));
            }
          }}
          onInputChange={(value) => {
            // The underlying Autocomplete also fires `onInputChange` to sync
            // its displayed text to the selected item's *label* right after
            // `onSelectionChange` already set the real key -- that sync is
            // undone here by mapping a known label back to its key. A value
            // that matches no label is real custom text.
            const matched = PROVIDER_CATEGORY_OPTIONS.find(
              (option) => option.label === value,
            );
            setForm((prev) => ({
              ...prev,
              providerCategory: matched ? matched.key : value,
            }));
          }}
          required
        />
        <SettingsInput
          testId="super-admin-oauth-provider-display-name"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_DISPLAY_NAME)}
          type="text"
          value={form.displayName}
          onChange={(value) =>
            setForm((prev) => ({ ...prev, displayName: value }))
          }
          showRequiredTag
          autoComplete="off"
        />
        <SettingsSwitch
          testId="super-admin-oauth-provider-is-idp"
          isToggled={form.isIdp}
          onToggle={(value) => setForm((prev) => ({ ...prev, isIdp: value }))}
        >
          {t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_IS_IDP)}
        </SettingsSwitch>
        <SettingsInput
          testId="super-admin-oauth-provider-client-id"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CLIENT_ID)}
          type="text"
          value={form.clientId}
          onChange={(value) =>
            setForm((prev) => ({ ...prev, clientId: value }))
          }
          showRequiredTag
          autoComplete="off"
        />
        <SettingsInput
          testId="super-admin-oauth-provider-client-secret"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CLIENT_SECRET)}
          type="password"
          value={form.clientSecret}
          placeholder={
            mode === "edit"
              ? t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CLIENT_SECRET_PLACEHOLDER)
              : undefined
          }
          hint={
            mode === "edit"
              ? t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CLIENT_SECRET_HINT)
              : undefined
          }
          onChange={(value) =>
            setForm((prev) => ({ ...prev, clientSecret: value }))
          }
          showOptionalTag
          autoComplete="new-password"
        />
        <SettingsInput
          testId="super-admin-oauth-provider-authorization-url"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_AUTH_URL)}
          type="text"
          value={form.authorizationUrl}
          onChange={(value) =>
            setForm((prev) => ({ ...prev, authorizationUrl: value }))
          }
          showOptionalTag
        />
        <SettingsInput
          testId="super-admin-oauth-provider-token-url"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_TOKEN_URL)}
          type="text"
          value={form.tokenUrl}
          onChange={(value) =>
            setForm((prev) => ({ ...prev, tokenUrl: value }))
          }
          showOptionalTag
        />
        <SettingsInput
          testId="super-admin-oauth-provider-userinfo-url"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_USERINFO_URL)}
          type="text"
          value={form.userinfoUrl}
          onChange={(value) =>
            setForm((prev) => ({ ...prev, userinfoUrl: value }))
          }
          showOptionalTag
        />
        <SettingsInput
          testId="super-admin-oauth-provider-scopes"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_SCOPES)}
          type="text"
          value={form.scopes}
          hint={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_SCOPES_HINT)}
          onChange={(value) => setForm((prev) => ({ ...prev, scopes: value }))}
          showOptionalTag
        />
        <SettingsInput
          testId="super-admin-oauth-provider-drift"
          label={t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_DRIFT)}
          type="number"
          min={0}
          value={form.permittedDriftSeconds}
          onChange={(value) =>
            setForm((prev) => ({ ...prev, permittedDriftSeconds: value }))
          }
          showOptionalTag
        />
      </div>
    </OrgModal>
  );
}

export function SuperAdminOAuthProviders() {
  const { t } = useTranslation();
  const copy = navCopy(SUPER_ADMIN_PATHS.oauthProviders);
  const { data, isLoading, isError } = useOAuthProviders();
  const createProvider = useCreateOAuthProvider();
  const updateProvider = useUpdateOAuthProvider();
  const deleteProvider = useDeleteOAuthProvider();

  const [formMode, setFormMode] = useState<"create" | "edit" | null>(null);
  const [editingProvider, setEditingProvider] = useState<OAuthProvider | null>(
    null,
  );
  const [providerToDelete, setProviderToDelete] =
    useState<OAuthProvider | null>(null);

  const providers = useMemo(() => data ?? [], [data]);

  const openCreate = () => {
    setEditingProvider(null);
    setFormMode("create");
  };

  const openEdit = (provider: OAuthProvider) => {
    setEditingProvider(provider);
    setFormMode("edit");
  };

  const closeForm = () => {
    setFormMode(null);
    setEditingProvider(null);
  };

  const handleSubmit = (form: OAuthProviderFormState) => {
    const payload = {
      provider_category: form.providerCategory.trim(),
      display_name: form.displayName.trim(),
      is_idp: form.isIdp,
      client_id: form.clientId.trim(),
      client_secret: form.clientSecret ? form.clientSecret : undefined,
      authorization_url: form.authorizationUrl.trim() || null,
      token_url: form.tokenUrl.trim() || null,
      userinfo_url: form.userinfoUrl.trim() || null,
      scopes: parseScopes(form.scopes),
      permitted_drift_seconds: form.permittedDriftSeconds
        ? Number(form.permittedDriftSeconds)
        : undefined,
    };

    if (formMode === "edit" && editingProvider) {
      updateProvider.mutate(
        { id: editingProvider.id, ...payload },
        { onSuccess: closeForm },
      );
    } else {
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
      data-testid="super-admin-oauth-providers"
    >
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          <BrandButton
            type="button"
            variant="primary"
            testId="super-admin-add-oauth-provider"
            startContent={<Plus className="h-4 w-4" />}
            onClick={openCreate}
          >
            {t(I18nKey.SUPER_ADMIN$ADD_OAUTH_PROVIDER)}
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
      <SuperAdminTable<OAuthProvider>
        testId="super-admin-oauth-providers-table"
        rows={providers}
        getRowKey={(row) => String(row.id)}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_OAUTH_PROVIDERS)}
        columns={[
          {
            key: "display_name",
            header: t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_DISPLAY_NAME),
            className: "w-[22%]",
            render: (row) => (
              <span className="block truncate">{row.display_name}</span>
            ),
          },
          {
            key: "provider_category",
            header: t(I18nKey.SUPER_ADMIN$COL_PROVIDER),
            className: "w-[18%]",
            render: (row) => (
              <span className="block truncate">{row.provider_category}</span>
            ),
          },
          {
            key: "type",
            header: t(I18nKey.SUPER_ADMIN$COL_TYPE),
            className: "w-[14%]",
            render: (row) =>
              row.is_idp
                ? t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_TYPE_IDP)
                : t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_TYPE_GIT),
          },
          {
            key: "client_id",
            header: t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_CLIENT_ID),
            className: "w-[22%]",
            render: (row) => (
              <span className="block truncate">{row.client_id}</span>
            ),
          },
          {
            key: "secret",
            header: t(I18nKey.SUPER_ADMIN$COL_SECRET),
            className: "w-[14%]",
            render: (row) =>
              row.has_client_secret
                ? t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_SECRET_SET)
                : t(I18nKey.SUPER_ADMIN$OAUTH_PROVIDER_SECRET_UNSET),
          },
          {
            key: "actions",
            header: "",
            className: "w-12 min-w-12 text-right",
            render: (row) => (
              <SuperAdminRowMenu
                testId={`super-admin-oauth-provider-actions-${row.id}`}
                ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                items={[
                  {
                    label: t(I18nKey.BUTTON$EDIT),
                    testId: `super-admin-oauth-provider-edit-${row.id}`,
                    onSelect: () => openEdit(row),
                  },
                  {
                    label: t(I18nKey.BUTTON$DELETE),
                    testId: `super-admin-oauth-provider-delete-${row.id}`,
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
        <OAuthProviderFormModal
          mode={formMode}
          initial={editingProvider ? toFormState(editingProvider) : EMPTY_FORM}
          isLoading={createProvider.isPending || updateProvider.isPending}
          onClose={closeForm}
          onSubmit={handleSubmit}
        />
      ) : null}
      {providerToDelete ? (
        <OrgModal
          testId="super-admin-oauth-provider-delete-confirm"
          title={t(I18nKey.SUPER_ADMIN$DELETE_OAUTH_PROVIDER_TITLE)}
          description={
            <Trans
              i18nKey={I18nKey.SUPER_ADMIN$DELETE_OAUTH_PROVIDER_CONFIRM}
              values={{ name: providerToDelete.display_name }}
              components={{ name: <span className="text-white" /> }}
            />
          }
          primaryButtonText={t(I18nKey.BUTTON$CONFIRM)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          primaryButtonTestId="super-admin-oauth-provider-delete-confirm-submit"
          secondaryButtonTestId="super-admin-oauth-provider-delete-confirm-cancel"
          onPrimaryClick={confirmDelete}
          onClose={() => setProviderToDelete(null)}
          isLoading={deleteProvider.isPending}
        />
      ) : null}
    </div>
  );
}
