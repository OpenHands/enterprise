import { useTranslation } from "react-i18next";
import { LicenseUsage } from "#/api/license-service/license-service.api";
import { useLicenseUsage } from "#/hooks/query/use-license-usage";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

export type LicenseLimitKind = Exclude<
  keyof LicenseUsage,
  "customer_center_url"
>;

export const LICENSE_LIMIT_MESSAGES: Record<LicenseLimitKind, I18nKey> = {
  users: I18nKey.LICENSE$USER_LIMIT_REACHED,
  orgs: I18nKey.LICENSE$ORG_LIMIT_REACHED,
};

/** Whether the install uses every user or organization its license allows. */
export function useLicenseLimitReached(kind: LicenseLimitKind): boolean {
  const { data: usage } = useLicenseUsage();
  const limit = usage?.[kind].limit;
  if (!usage || limit === null || limit === undefined) return false;
  return usage[kind].used >= limit;
}

/**
 * Wraps the click handler of a button that adds a user or an organization.
 * At the limit, the click shows the limit error and does nothing else.
 */
export function useLicenseLimitGuard(kind: LicenseLimitKind) {
  const { t } = useTranslation();
  const reached = useLicenseLimitReached(kind);
  return (action: () => void) => () => {
    if (reached) {
      displayErrorToast(t(LICENSE_LIMIT_MESSAGES[kind]));
      return;
    }
    action();
  };
}
