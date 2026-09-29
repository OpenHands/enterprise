import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";

// Marks an integration whose connection is shared by the whole organization.
// Hidden in personal orgs, where the connection is not org-wide.
export function OrgScopeBadge() {
  const { t } = useTranslation();
  const { isTeamOrg } = useOrgTypeAndAccess();

  if (!isTeamOrg) return null;

  return (
    <span
      data-testid="org-scope-badge"
      className="shrink-0 rounded bg-[var(--oh-surface-raised)] px-1.5 py-0.5 text-xs text-muted"
    >
      {t(I18nKey.COMMON$ORGANIZATION)}
    </span>
  );
}
