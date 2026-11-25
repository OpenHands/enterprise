import { type Key, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { FaChevronLeft } from "react-icons/fa6";
import { SettingsDropdownInput } from "#/components/features/settings/settings-dropdown-input";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import type { WarmRuntimeAccess } from "#/api/warm-runtimes-service/warm-runtimes.types";
import { useUpdateWarmRuntimeAccess } from "#/hooks/mutation/use-warm-runtime-mutations";
import { useSuperAdminTeamOrganizations } from "#/hooks/query/use-super-admin";
import { useWarmRuntimeAccess } from "#/hooks/query/use-warm-runtimes";
import { cn } from "#/utils/utils";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { BrandButton, SuperAdminPageHeader } from "./super-admin-chrome";

interface AllowedOrg {
  id: string;
  name: string;
}

function PermissionsForm({
  name,
  access,
}: {
  name: string;
  access: WarmRuntimeAccess;
}) {
  const { t } = useTranslation();
  const updateAccess = useUpdateWarmRuntimeAccess();
  const [isPrivate, setIsPrivate] = useState(access.is_private);
  const [allowedOrgs, setAllowedOrgs] = useState<AllowedOrg[]>(access.orgs);
  const { data: teamOrgs, isLoading: orgsLoading } =
    useSuperAdminTeamOrganizations(isPrivate);

  const allowedIds = new Set(allowedOrgs.map((org) => org.id));
  const candidates = (teamOrgs ?? [])
    .filter((org) => !allowedIds.has(org.id))
    .map((org) => ({ key: org.id, label: org.name }));

  const isDirty =
    isPrivate !== access.is_private ||
    (isPrivate &&
      JSON.stringify(allowedOrgs.map((org) => org.id).sort()) !==
        JSON.stringify(access.orgs.map((org) => org.id).sort()));

  const addOrg = (key: Key | null) => {
    const org = teamOrgs?.find((item) => item.id === key);
    if (org) {
      setAllowedOrgs((prev) => [...prev, { id: org.id, name: org.name }]);
    }
  };

  const save = () =>
    updateAccess.mutate({
      name,
      is_private: isPrivate,
      org_ids: isPrivate ? allowedOrgs.map((org) => org.id) : [],
    });

  return (
    <div
      data-testid="super-admin-warm-runtime-permissions-form"
      className="flex max-w-2xl flex-col gap-6"
    >
      <SettingsSwitch
        testId="super-admin-warm-runtime-private"
        isToggled={isPrivate}
        onToggle={setIsPrivate}
        hint={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PRIVATE_HINT)}
      >
        {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PRIVATE)}
      </SettingsSwitch>

      {isPrivate ? (
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-white">
            {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ALLOWED_ORGS)}
          </h3>
          {allowedOrgs.length === 0 ? (
            <p
              data-testid="super-admin-warm-runtime-no-allowed-orgs"
              className="text-xs text-tertiary-alt"
            >
              {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_NO_ALLOWED_ORGS)}
            </p>
          ) : null}
          <ul className="flex flex-col gap-2">
            {allowedOrgs.map((org) => (
              <li
                key={org.id}
                data-testid={`super-admin-warm-runtime-allowed-org-${org.id}`}
                className="flex items-center justify-between gap-2 text-sm"
              >
                <span className="min-w-0 truncate">{org.name}</span>
                <BrandButton
                  testId={`super-admin-warm-runtime-remove-org-${org.id}`}
                  type="button"
                  variant="secondary"
                  onClick={() =>
                    setAllowedOrgs((prev) =>
                      prev.filter((item) => item.id !== org.id),
                    )
                  }
                >
                  {t(I18nKey.SUPER_ADMIN$REMOVE)}
                </BrandButton>
              </li>
            ))}
          </ul>
          <SettingsDropdownInput
            testId="super-admin-warm-runtime-add-org"
            name="warm_runtime_add_org"
            label={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_ADD_ORG)}
            placeholder={t(I18nKey.ORG$SELECT_ORGANIZATION_PLACEHOLDER)}
            items={candidates}
            selectedKey={null}
            isLoading={orgsLoading}
            onSelectionChange={addOrg}
          />
        </div>
      ) : null}

      <BrandButton
        testId="super-admin-warm-runtime-permissions-submit"
        type="button"
        variant="primary"
        className="self-start"
        isDisabled={!isDirty || updateAccess.isPending}
        onClick={save}
      >
        {t(I18nKey.SETTINGS$SAVE_CHANGES)}
      </BrandButton>
    </div>
  );
}

/** Super Admin page that decides who may use a warm runtime
 * (`/super-admin/warm-runtimes/permissions/{name}`). */
export function SuperAdminWarmRuntimePermissions() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { name = "" } = useParams<{ name: string }>();
  const { data: access, isLoading, isError } = useWarmRuntimeAccess(name);

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-warm-runtime-permissions"
    >
      <button
        type="button"
        data-testid="super-admin-warm-runtime-permissions-back"
        onClick={() => navigate(SUPER_ADMIN_PATHS.warmRuntimes)}
        className={cn(
          "inline-flex w-fit shrink-0 items-center gap-1.5",
          "text-xs font-medium text-[var(--oh-muted)] hover:text-white",
          "transition-colors",
        )}
      >
        <FaChevronLeft size={10} aria-hidden="true" />
        {t(I18nKey.SUPER_ADMIN$BACK_TO_WARM_RUNTIMES)}
      </button>
      <SuperAdminPageHeader
        title={t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_PERMISSIONS)}
        subtitle={name}
      />
      {isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {isError ? (
        <p
          data-testid="super-admin-warm-runtime-permissions-error"
          className="text-sm text-red-400"
        >
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      {access ? (
        <PermissionsForm
          key={JSON.stringify(access)}
          name={name}
          access={access}
        />
      ) : null}
    </div>
  );
}
