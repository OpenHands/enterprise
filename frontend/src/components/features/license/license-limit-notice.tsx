import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useLicenseUsage } from "#/hooks/query/use-license-usage";
import {
  LICENSE_LIMIT_MESSAGES,
  LicenseLimitKind,
  useLicenseLimitReached,
} from "#/hooks/use-license-limit";

/**
 * Says that the install has reached one of its licensed limits, with a link
 * to upgrading. It renders nothing below the limit, or when the license sets
 * no such limit.
 */
export function LicenseLimitNotice({ kind }: { kind: LicenseLimitKind }) {
  const { t } = useTranslation();
  const { data: usage } = useLicenseUsage();
  const reached = useLicenseLimitReached(kind);

  if (!usage || !reached) return null;

  return (
    <div
      data-testid={`license-limit-notice-${kind}`}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-xs leading-5 bg-amber-500/15 text-amber-200 border-amber-500/40"
    >
      <span>{t(LICENSE_LIMIT_MESSAGES[kind])}</span>
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
