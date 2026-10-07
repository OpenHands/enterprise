import React from "react";
import { useTranslation } from "react-i18next";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useSwitchOrganization } from "#/hooks/mutation/use-switch-organization";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { useIsSuperAdmin } from "#/hooks/query/use-is-super-admin";
import { useShouldHideOrgSelector } from "#/hooks/use-should-hide-org-selector";
import { I18nKey } from "#/i18n/declaration";
import { Organization } from "#/types/org";
import { Dropdown } from "#/ui/dropdown/dropdown";

/** Sentinel option value for a super admin's "All Organizations" selection. */
const ALL_ORGANIZATIONS_VALUE = "";

export function OrgSelector() {
  const { t } = useTranslation();
  const { organizationId, explicitlyNoOrg, clearOrganizationId } =
    useSelectedOrganizationId();
  const { data, isLoading } = useOrganizations();
  const organizations = data?.organizations;
  const { mutate: switchOrganization, isPending: isSwitching } =
    useSwitchOrganization();
  const shouldHideSelector = useShouldHideOrgSelector();
  const { data: isSuperAdmin } = useIsSuperAdmin();

  const getOrgDisplayName = React.useCallback(
    (org: Organization) =>
      org.is_personal ? t(I18nKey.ORG$PERSONAL_WORKSPACE) : org.name,
    [t],
  );

  const getOrgDisplayNameOrEmpty = (org: Organization | undefined) =>
    org ? getOrgDisplayName(org) : "";

  const selectedOrg = React.useMemo(() => {
    if (organizationId) {
      return organizations?.find((org) => org.id === organizationId);
    }

    return explicitlyNoOrg ? undefined : organizations?.[0];
  }, [organizationId, explicitlyNoOrg, organizations]);

  if (shouldHideSelector) {
    return null;
  }

  return (
    <Dropdown
      testId="org-selector"
      key={`${selectedOrg?.id}-${selectedOrg?.name}-${explicitlyNoOrg}`}
      searchable={false}
      defaultValue={{
        label: explicitlyNoOrg
          ? t(I18nKey.ORG$ALL_ORGANIZATIONS)
          : getOrgDisplayNameOrEmpty(selectedOrg),
        value: explicitlyNoOrg
          ? ALL_ORGANIZATIONS_VALUE
          : selectedOrg?.id || "",
      }}
      onChange={(item) => {
        if (!item) return;
        if (item.value === ALL_ORGANIZATIONS_VALUE) {
          if (!explicitlyNoOrg) clearOrganizationId();
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
      }}
      placeholder={t(I18nKey.ORG$SELECT_ORGANIZATION_PLACEHOLDER)}
      loading={isLoading || isSwitching}
      options={[
        ...(isSuperAdmin
          ? [
              {
                value: ALL_ORGANIZATIONS_VALUE,
                label: t(I18nKey.ORG$ALL_ORGANIZATIONS),
              },
            ]
          : []),
        ...(organizations?.map((org) => ({
          value: org.id,
          label: getOrgDisplayName(org),
        })) || []),
      ]}
    />
  );
}
