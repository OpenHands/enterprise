import React from "react";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useOrganization } from "#/hooks/query/use-organization";
import { useMe } from "#/hooks/query/use-me";
import { useConfig } from "#/hooks/query/use-config";
import { I18nKey } from "#/i18n/declaration";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { DeleteOrgConfirmationModal } from "#/components/features/org/delete-org-confirmation-modal";
import { GitConversationRouting } from "#/components/features/org/git-conversation-routing";
import { ChangeOrgNameModal } from "#/components/features/org/change-org-name-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useOrganizations } from "#/hooks/query/use-organizations";
import { Text } from "#/ui/typography";
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
  const { data: config } = useConfig();
  const { data: orgsData } = useOrganizations();

  const role = me?.role ?? "member";
  const { hasPermission } = usePermission(role);

  const [changeOrgNameFormVisible, setChangeOrgNameFormVisible] =
    React.useState(false);
  const [deleteOrgConfirmationVisible, setDeleteOrgConfirmationVisible] =
    React.useState(false);

  const canChangeOrgName = !!me && hasPermission("change_organization_name");
  const canDeleteOrg = !!me && hasPermission("delete_organization");
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

      <div data-testid="org-name" className="flex w-full flex-col gap-2.5">
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

      {canManageOrgClaims && !hideGitConversationRouting && (
        <div className="mt-2 w-full border-t border-[var(--oh-border)] pt-6">
          <GitConversationRouting />
        </div>
      )}

      {canDeleteOrg && (
        <section
          data-testid="delete-org-bar"
          className="mt-auto flex w-full flex-col items-stretch gap-3 border-t border-[var(--oh-border)] pt-6"
        >
          <Text className="text-lg font-medium text-white">
            {t(I18nKey.ORG$DANGER_ZONE)}
          </Text>
          <div className="flex items-center justify-between gap-4 rounded-xl border border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.08)] px-4 py-3">
            <p className="min-w-0 text-sm leading-5 text-[var(--oh-color-danger)]">
              {t(I18nKey.ORG$DANGER_ZONE_DELETE_BAR)}
            </p>
            <BrandButton
              type="button"
              variant="danger"
              className="shrink-0"
              startContent={<Trash2 className="h-4 w-4" />}
              onClick={() => setDeleteOrgConfirmationVisible(true)}
            >
              {t(I18nKey.ORG$DELETE_ORGANIZATION)}
            </BrandButton>
          </div>
        </section>
      )}
    </div>
  );
}

export default ManageOrg;
