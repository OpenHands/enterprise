import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useSsoLicensed } from "#/hooks/query/use-sso-licensed";

/**
 * Says that the license doesn't include SSO, with a link to upgrading. It
 * renders nothing when the license includes SSO.
 */
export function SsoNotLicensedNotice() {
  const { t } = useTranslation();
  const { ssoLicensed, customerCenterUrl } = useSsoLicensed();
  if (ssoLicensed) return null;

  return (
    <div
      data-testid="sso-not-licensed-notice"
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border px-3 py-2 text-xs leading-5 bg-amber-500/15 text-amber-200 border-amber-500/40"
    >
      <span>{t(I18nKey.LICENSE$SSO_NOT_INCLUDED)}</span>
      {customerCenterUrl && (
        <a
          href={customerCenterUrl}
          target="_blank"
          rel="noreferrer"
          className="underline underline-offset-2 hover:opacity-80"
        >
          {t(I18nKey.LICENSE$UPGRADE)}
        </a>
      )}
    </div>
  );
}
