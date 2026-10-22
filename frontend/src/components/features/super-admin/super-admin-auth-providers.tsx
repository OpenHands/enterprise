import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { Plus } from "lucide-react";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { Toggle } from "#/components/shared/toggle/toggle";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import {
  useCreateAuthProvider,
  useDeleteAuthProvider,
  useUpdateAuthProvider,
} from "#/hooks/mutation/use-auth-provider-mutations";
import { useAuthProviders } from "#/hooks/query/use-auth-providers";
import type { AuthProvider } from "#/api/auth-providers-service/auth-providers.types";
import { I18nKey } from "#/i18n/declaration";
import {
  SUPER_ADMIN_NAV_ITEMS,
  SUPER_ADMIN_PATHS,
} from "#/constants/super-admin-nav";
import {
  BrandButton,
  SuperAdminPageHeader,
  SuperAdminRowMenu,
  SuperAdminTable,
} from "./super-admin-chrome";
import { KIND_OAUTH, kindLabel } from "./auth-provider-form";
import {
  AUTH_PROVIDER_PRESETS,
  type AuthProviderPresetKey,
} from "./auth-provider-presets";

function navCopy(path: string) {
  return (
    SUPER_ADMIN_NAV_ITEMS.find((item) => item.to === path) ??
    SUPER_ADMIN_NAV_ITEMS[0]
  );
}

/** Creates a provider from a preset with placeholder values, then hands off
 * to its edit page: the external IdP usually needs this row's callback/
 * metadata URL (which needs a row id) before it will hand over the details
 * needed to finish configuring it here. */
function CreateAuthProviderModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const createProvider = useCreateAuthProvider();
  const [presetKey, setPresetKey] = useState<AuthProviderPresetKey | null>(
    null,
  );

  const items = AUTH_PROVIDER_PRESETS.map((preset) => ({
    key: preset.key,
    label: t(preset.label),
  }));

  const handleCreate = () => {
    const preset = AUTH_PROVIDER_PRESETS.find(
      (candidate) => candidate.key === presetKey,
    );
    if (!preset) {
      return;
    }
    createProvider.mutate(preset.params, {
      onSuccess: (created) =>
        navigate(`${SUPER_ADMIN_PATHS.authProviders}/${created.id}`),
    });
  };

  return (
    <OrgModal
      testId="super-admin-auth-provider-create"
      title={t(I18nKey.SUPER_ADMIN$ADD_AUTH_PROVIDER)}
      description={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_CREATE_HINT)}
      primaryButtonText={t(I18nKey.SUPER_ADMIN$CREATE_AUTH_PROVIDER)}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      primaryButtonTestId="super-admin-auth-provider-create-submit"
      secondaryButtonTestId="super-admin-auth-provider-create-cancel"
      isPrimaryDisabled={!presetKey}
      isLoading={createProvider.isPending}
      onPrimaryClick={handleCreate}
      onClose={onClose}
      className="w-md max-w-full"
    >
      <SettingsDropdownInput
        testId="super-admin-auth-provider-type"
        name="provider_type"
        label={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_TYPE)}
        placeholder={t(I18nKey.SUPER_ADMIN$AUTH_PROVIDER_TYPE_PLACEHOLDER)}
        items={items}
        selectedKey={presetKey}
        onSelectionChange={(key) =>
          setPresetKey((key as AuthProviderPresetKey | null) ?? null)
        }
        required
      />
    </OrgModal>
  );
}

export function SuperAdminAuthProviders() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const copy = navCopy(SUPER_ADMIN_PATHS.authProviders);
  const { data, isLoading, isError } = useAuthProviders();
  const updateProvider = useUpdateAuthProvider();
  const deleteProvider = useDeleteAuthProvider();

  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [providerToDelete, setProviderToDelete] = useState<AuthProvider | null>(
    null,
  );

  const providers = useMemo(() => data ?? [], [data]);

  const openEdit = (provider: AuthProvider) =>
    navigate(`${SUPER_ADMIN_PATHS.authProviders}/${provider.id}`);

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
            onClick={() => setIsCreateOpen(true)}
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
      {isCreateOpen ? (
        <CreateAuthProviderModal onClose={() => setIsCreateOpen(false)} />
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
