import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useUpdateSuperAdminUserGroups } from "#/hooks/mutation/use-super-admin-mutations";
import { I18nKey } from "#/i18n/declaration";
import type { SuperAdminOrgRole } from "./super-admin-mock";

/**
 * Asks a Super Admin to join an organization before they can open it.
 * Admin is the default so joining to inspect the org does not take the
 * owner role from the person who already owns it.
 */
export function SuperAdminGrantSelfAccessModal({
  orgId,
  orgName,
  userId,
  onClose,
  onGranted,
}: {
  orgId: string;
  orgName: string;
  userId: string | null;
  onClose: () => void;
  onGranted: () => void;
}) {
  const { t } = useTranslation();
  const updateGroups = useUpdateSuperAdminUserGroups();
  const [role, setRole] = useState<SuperAdminOrgRole>("admin");

  const grantAccess = () => {
    if (!userId || updateGroups.isPending) {
      return;
    }
    updateGroups.mutate(
      {
        userId,
        action: "add",
        orgIds: [orgId],
        role,
      },
      { onSuccess: onGranted },
    );
  };

  return (
    <OrgModal
      testId="super-admin-grant-self-access"
      title={t(I18nKey.SUPER_ADMIN$GRANT_SELF_ACCESS_TITLE)}
      description={t(I18nKey.SUPER_ADMIN$GRANT_SELF_ACCESS_DESCRIPTION, {
        name: orgName,
      })}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      primaryButtonText={t(I18nKey.SUPER_ADMIN$GRANT_SELF_ACCESS_CONFIRM)}
      primaryButtonTestId="super-admin-grant-self-access-confirm"
      secondaryButtonTestId="super-admin-grant-self-access-cancel"
      onPrimaryClick={grantAccess}
      onClose={onClose}
      isLoading={updateGroups.isPending}
      isPrimaryDisabled={!userId}
    >
      <label className="flex w-full flex-col gap-1.5 text-sm">
        <span className="text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$GRANT_SELF_ACCESS_ROLE)}
        </span>
        <select
          data-testid="super-admin-grant-self-access-role"
          className="rounded-lg border border-[var(--oh-border)] bg-[var(--oh-surface)] px-3 py-2 text-foreground"
          value={role}
          onChange={(event) =>
            setRole(event.target.value as SuperAdminOrgRole)
          }
        >
          <option value="admin">{t(I18nKey.ORG$ROLE_ADMIN)}</option>
          <option value="owner">{t(I18nKey.ORG$ROLE_OWNER)}</option>
          <option value="member">{t(I18nKey.ORG$ROLE_MEMBER)}</option>
        </select>
      </label>
    </OrgModal>
  );
}
