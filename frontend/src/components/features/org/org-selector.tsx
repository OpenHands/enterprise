import React from "react";
import ReactDOM from "react-dom";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useSwitchOrganization } from "#/hooks/mutation/use-switch-organization";
import { useConfig } from "#/hooks/query/use-config";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { useMe } from "#/hooks/query/use-me";
import { useShouldHideOrgSelector } from "#/hooks/use-should-hide-org-selector";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { I18nKey } from "#/i18n/declaration";
import { Organization } from "#/types/org";
import { Dropdown } from "#/ui/dropdown/dropdown";
import { dropdownMenuRowClassName } from "#/utils/dropdown-classes";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import { CreateOrganizationModal } from "./create-organization-modal";

/** Sentinel value so the dashboard row is not treated as an organization id. */
const SUPER_ADMIN_OPTION_VALUE = "__super_admin__";

export function OrgSelector({
  alwaysVisible = false,
}: {
  /** Keep the menu on the Super Admin dashboard even with a single workspace. */
  alwaysVisible?: boolean;
} = {}) {
  const { t } = useTranslation();
  const { organizationId } = useSelectedOrganizationId();
  const { data, isLoading } = useOrganizations();
  const { data: me } = useMe();
  const { data: config } = useConfig();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const organizations = data?.organizations;
  const { mutate: switchOrganization, isPending: isSwitching } =
    useSwitchOrganization();
  const shouldHideSelector = useShouldHideOrgSelector();
  const [createOrganizationModalIsOpen, setCreateOrganizationModalIsOpen] =
    React.useState(false);

  const canCreateOrganization =
    me?.permissions?.includes("create_organization") === true;
  const showSuperAdmin = canAccessSuperAdminDashboard(
    config?.feature_flags,
    me?.permissions,
  );
  const onSuperAdmin = pathname.startsWith("/super-admin");
  const superAdminLabel = t(I18nKey.SUPER_ADMIN$ORG_MENU);

  const getOrgDisplayName = React.useCallback(
    (org: Organization) =>
      org.is_personal ? t(I18nKey.ORG$PERSONAL_WORKSPACE) : org.name,
    [t],
  );

  const selectedOrg = React.useMemo(() => {
    if (organizationId) {
      return organizations?.find((org) => org.id === organizationId);
    }

    return organizations?.[0];
  }, [organizationId, organizations]);

  if (shouldHideSelector && !alwaysVisible) {
    return null;
  }

  const orgOptions =
    organizations?.map((org) => ({
      value: org.id,
      label: getOrgDisplayName(org),
    })) || [];
  const options = showSuperAdmin
    ? [
        ...orgOptions,
        {
          value: SUPER_ADMIN_OPTION_VALUE,
          label: superAdminLabel,
          divider: orgOptions.length > 0,
        },
      ]
    : orgOptions;
  const selectedValue =
    onSuperAdmin && showSuperAdmin
      ? { label: superAdminLabel, value: SUPER_ADMIN_OPTION_VALUE }
      : {
          label: selectedOrg ? getOrgDisplayName(selectedOrg) : "",
          value: selectedOrg?.id || "",
        };

  return (
    <>
      <Dropdown
        testId="org-selector"
        key={`${onSuperAdmin ? "super-admin" : selectedOrg?.id}-${selectedOrg?.name}`}
        searchable={false}
        defaultValue={selectedValue}
        onChange={(item) => {
          if (!item || item.value === selectedValue.value) {
            return;
          }
          if (item.value === SUPER_ADMIN_OPTION_VALUE) {
            if (!onSuperAdmin) {
              navigate(SUPER_ADMIN_PATHS.root);
            }
            return;
          }
          if (item.value !== organizationId) {
            const org = organizations?.find((o) => o.id === item.value);
            switchOrganization({
              orgId: item.value,
              orgName: item.label,
              isPersonal: org?.is_personal ?? false,
            });
          }
          if (onSuperAdmin) {
            navigate("/settings");
          }
        }}
        placeholder={t(I18nKey.ORG$SELECT_ORGANIZATION_PLACEHOLDER)}
        loading={isLoading || isSwitching}
        options={options}
        footer={
          canCreateOrganization ? (
            <button
              type="button"
              data-testid="org-selector-create"
              className={dropdownMenuRowClassName}
              onClick={() => setCreateOrganizationModalIsOpen(true)}
            >
              <Plus className="size-4 shrink-0" aria-hidden />
              {t(I18nKey.ORG$CREATE_ORGANIZATION)}
            </button>
          ) : null
        }
      />
      {createOrganizationModalIsOpen &&
        ReactDOM.createPortal(
          <CreateOrganizationModal
            contactEmail={me?.email}
            contactName={
              organizations?.find((org) => org.is_personal)?.contact_name
            }
            onClose={() => setCreateOrganizationModalIsOpen(false)}
          />,
          document.getElementById("portal-root") || document.body,
        )}
    </>
  );
}
