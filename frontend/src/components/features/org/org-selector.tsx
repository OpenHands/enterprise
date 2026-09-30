import React from "react";
import ReactDOM from "react-dom";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useSwitchOrganization } from "#/hooks/mutation/use-switch-organization";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { useMe } from "#/hooks/query/use-me";
import { useShouldHideOrgSelector } from "#/hooks/use-should-hide-org-selector";
import { I18nKey } from "#/i18n/declaration";
import { Organization } from "#/types/org";
import { Dropdown } from "#/ui/dropdown/dropdown";
import { dropdownMenuRowClassName } from "#/utils/dropdown-classes";
import { CreateOrganizationModal } from "./create-organization-modal";

export function OrgSelector() {
  const { t } = useTranslation();
  const { organizationId } = useSelectedOrganizationId();
  const { data, isLoading } = useOrganizations();
  const { data: me } = useMe();
  const organizations = data?.organizations;
  const { mutate: switchOrganization, isPending: isSwitching } =
    useSwitchOrganization();
  const shouldHideSelector = useShouldHideOrgSelector();
  const [createOrganizationModalIsOpen, setCreateOrganizationModalIsOpen] =
    React.useState(false);

  const canCreateOrganization =
    me?.permissions?.includes("create_organization") === true;

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

  if (shouldHideSelector) {
    return null;
  }

  return (
    <>
      <Dropdown
        testId="org-selector"
        key={`${selectedOrg?.id}-${selectedOrg?.name}`}
        searchable={false}
        defaultValue={{
          label: selectedOrg ? getOrgDisplayName(selectedOrg) : "",
          value: selectedOrg?.id || "",
        }}
        onChange={(item) => {
          if (item && item.value !== organizationId) {
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
        options={
          organizations?.map((org) => ({
            value: org.id,
            label: getOrgDisplayName(org),
          })) || []
        }
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
