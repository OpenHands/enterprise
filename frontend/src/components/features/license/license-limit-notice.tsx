import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { LicenseUsage } from "#/api/license-service/license-service.api";
import { useLicenseUsage } from "#/hooks/query/use-license-usage";

export type LicenseLimitKind = Exclude<
  keyof LicenseUsage,
  "customer_center_url"
>;

const MESSAGES: Record<LicenseLimitKind, I18nKey> = {
  users: I18nKey.LICENSE$USER_LIMIT_REACHED,
  orgs: I18nKey.LICENSE$ORG_LIMIT_REACHED,
};

/** Whether the install is at or over one of its licensed limits. */
export function isAtLicenseLimit(
  usage: LicenseUsage | undefined,
  kind: LicenseLimitKind,
): boolean {
  const limit = usage?.[kind].limit;
  return limit !== null && limit !== undefined && usage![kind].used >= limit;
}

/**
 * Says that the install has reached one of its licensed limits, with a link
 * to upgrading. It renders nothing below the limit, or when the license sets
 * no such limit.
 */
export function LicenseLimitNotice({ kind }: { kind: LicenseLimitKind }) {
  const { t } = useTranslation();
  const { data: usage } = useLicenseUsage();

  if (!usage || !isAtLicenseLimit(usage, kind)) return null;

  return (
    <div
      data-testid={`license-limit-notice-${kind}`}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-xs leading-5 bg-amber-500/15 text-amber-200 border-amber-500/40"
    >
      <span>{t(MESSAGES[kind])}</span>
      <a
        href={usage.customer_center_url}
        target="_blank"
        rel="noreferrer"
        className="underline underline-offset-2 hover:opacity-80"
      >
        {t(I18nKey.LICENSE$UPGRADE)}
      </a>
    </div>
  );
}
