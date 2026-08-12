import React from "react";
import { useTranslation } from "react-i18next";
import { useOrganization } from "#/hooks/query/use-organization";
import { useMe } from "#/hooks/query/use-me";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { isBillingHidden } from "#/utils/org/billing-visibility";
import { DeleteOrgConfirmationModal } from "#/components/features/org/delete-org-confirmation-modal";
import { GitConversationRouting } from "#/components/features/org/git-conversation-routing";
import { ChangeOrgNameModal } from "#/components/features/org/change-org-name-modal";
import { AddCreditsModal } from "#/components/features/org/add-credits-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useBalance } from "#/hooks/query/use-balance";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { cn } from "#/utils/utils";
import {
  formControlHeightClassName,
  formControlRadiusClassName,
  formControlBorderClassName,
  formControlSurfaceClassName,
} from "#/utils/form-control-classes";

export const clientLoader = createPermissionGuard("view_billing");

function ManageOrg() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const { data: organization } = useOrganization();
  const { data: balance } = useBalance();
  const { data: config } = useConfig();
  const { data: orgsData } = useOrganizations();

  const role = me?.role ?? "member";
  const { hasPermission } = usePermission(role);

  const [addCreditsFormVisible, setAddCreditsFormVisible] =
    React.useState(false);
  const [changeOrgNameFormVisible, setChangeOrgNameFormVisible] =
    React.useState(false);
  const [deleteOrgConfirmationVisible, setDeleteOrgConfirmationVisible] =
    React.useState(false);

  const canChangeOrgName = !!me && hasPermission("change_organization_name");
  const canDeleteOrg = !!me && hasPermission("delete_organization");
  const canAddCredits = !!me && hasPermission("add_credits");
  const canManageOrgClaims = !!me && hasPermission("manage_org_claims");
  // In org-only installs with a single visible org, git claims are not
  // needed: resolver conversations follow the user's current org and
  // unclaimed automation events fall back to the default org. The section
  // reappears as soon as a second org exists, when claims become the
  // routing mechanism again.
  const hideGitConversationRouting =
    config?.feature_flags?.hide_personal_workspaces === true &&
    orgsData?.organizations?.length === 1 &&
    orgsData.organizations[0]?.is_personal !== true;
  const shouldHideBilling = isBillingHidden(
    config,
    hasPermission("view_billing"),
  );

  return (
    <div
      data-testid="manage-org-screen"
      className="flex w-full flex-col items-start gap-6"
    >
      {changeOrgNameFormVisible && (
        <ChangeOrgNameModal
          onClose={() => setChangeOrgNameFormVisible(false)}
        />
      )}
      {deleteOrgConfirmationVisible && (
        <DeleteOrgConfirmationModal
          onClose={() => setDeleteOrgConfirmationVisible(false)}
        />
      )}

      {!shouldHideBilling && (
        <div data-testid="org-credits" className="flex w-sm flex-col gap-2.5">
          <span className="text-sm">{t(I18nKey.ORG$CREDITS)}</span>
          <div className="flex items-center gap-2">
            <div
              className={cn(
                formControlHeightClassName,
                formControlRadiusClassName,
                formControlBorderClassName,
                formControlSurfaceClassName,
                "flex min-w-0 flex-1 items-center px-3 text-sm text-white",
              )}
            >
              <span
                data-testid="available-credits"
                className="min-w-0 truncate font-medium tabular-nums tracking-tight text-white"
              >
                ${Number(balance ?? 0).toFixed(2)}
              </span>
            </div>
            {canAddCredits && (
              <BrandButton
                type="button"
                variant="primary"
                onClick={() => setAddCreditsFormVisible(true)}
              >
                {t(I18nKey.ORG$ADD)}
              </BrandButton>
            )}
          </div>
        </div>
      )}

      {addCreditsFormVisible && !shouldHideBilling && (
        <AddCreditsModal onClose={() => setAddCreditsFormVisible(false)} />
      )}

      <div data-testid="org-name" className="flex w-sm flex-col gap-2.5">
        <span className="text-sm">{t(I18nKey.ORG$ORGANIZATION_NAME)}</span>

        <div
          className={cn(
            formControlHeightClassName,
            formControlRadiusClassName,
            formControlBorderClassName,
            formControlSurfaceClassName,
            "flex w-full items-center justify-between px-3 text-sm text-white",
          )}
        >
          <span className="min-w-0 truncate text-white">
            {organization?.name}
          </span>
          {canChangeOrgName && (
            <button
              type="button"
              onClick={() => setChangeOrgNameFormVisible(true)}
              className="shrink-0 cursor-pointer text-sm font-normal leading-5 text-[var(--oh-muted)] transition-[color] duration-75 hover:text-white motion-reduce:transition-none"
            >
              {t(I18nKey.ORG$CHANGE)}
            </button>
          )}
        </div>
      </div>

      {canDeleteOrg && (
        <BrandButton
          type="button"
          variant="ghost-danger"
          onClick={() => setDeleteOrgConfirmationVisible(true)}
        >
          {t(I18nKey.ORG$DELETE_ORGANIZATION)}
        </BrandButton>
      )}

      {canManageOrgClaims && !hideGitConversationRouting && (
        <div className="mt-2 w-full border-t border-[var(--oh-border)] pt-6">
          <GitConversationRouting />
        </div>
      )}
    </div>
  );
}

export default ManageOrg;
