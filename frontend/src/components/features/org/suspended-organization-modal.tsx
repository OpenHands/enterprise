import { useTranslation } from "react-i18next";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { ModalBody } from "#/components/shared/modals/modal-body";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useSwitchOrganization } from "#/hooks/mutation/use-switch-organization";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { I18nKey } from "#/i18n/declaration";
import { OrganizationSuspensionReason } from "#/stores/suspended-organization-store";
import { Organization } from "#/types/org";
import OpenHandsLogo from "#/assets/branding/openhands-logo.svg?react";

interface SuspendedOrganizationModalProps {
  organizationId: string;
  reason: OrganizationSuspensionReason;
}

export function SuspendedOrganizationModal({
  organizationId,
  reason,
}: SuspendedOrganizationModalProps) {
  const { t } = useTranslation();
  const { data } = useOrganizations();
  const { mutate: switchOrganization, isPending: isSwitching } =
    useSwitchOrganization();

  const getOrgDisplayName = (org: Organization) =>
    org.is_personal ? t(I18nKey.ORG$PERSONAL_WORKSPACE) : org.name;

  const organizations = data?.organizations ?? [];
  const suspendedOrg = organizations.find((org) => org.id === organizationId);
  const otherOrgs = organizations.filter((org) => org.id !== organizationId);
  const name = suspendedOrg ? getOrgDisplayName(suspendedOrg) : "";

  return (
    <ModalBackdrop>
      <ModalBody
        testID="suspended-organization-modal"
        className="border border-tertiary"
      >
        <OpenHandsLogo width={68} height={46} />
        <div className="flex flex-col gap-2 w-full items-center text-center">
          <h1 className="text-2xl font-bold">
            {t(I18nKey.ORG$ACCESS_SUSPENDED)}
          </h1>
          <p role="alert" className="text-sm text-muted-foreground">
            {t(
              reason === "organization"
                ? I18nKey.ORG$ORGANIZATION_SUSPENDED
                : I18nKey.ORG$MEMBERSHIP_SUSPENDED,
              { name },
            )}
          </p>
        </div>
        {otherOrgs.length > 0 && (
          <div className="flex flex-col gap-2 w-full">
            <p className="text-sm font-medium text-center">
              {t(I18nKey.ORG$SWITCH_TO_ANOTHER_ORGANIZATION)}
            </p>
            {otherOrgs.map((org) => (
              <BrandButton
                key={org.id}
                type="button"
                variant="secondary"
                className="w-full"
                isDisabled={isSwitching}
                onClick={() =>
                  switchOrganization({
                    orgId: org.id,
                    orgName: getOrgDisplayName(org),
                    isPersonal: org.is_personal ?? false,
                  })
                }
              >
                {getOrgDisplayName(org)}
              </BrandButton>
            ))}
          </div>
        )}
      </ModalBody>
    </ModalBackdrop>
  );
}
