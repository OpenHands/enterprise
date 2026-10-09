import { useTranslation } from "react-i18next";
import {
  PRIVACY_POLICY_URL,
  TERMS_OF_SERVICE_URL,
} from "#/constants/legal-links";
import { I18nKey } from "#/i18n/declaration";

const FOOTER_LINKS = [
  {
    label: I18nKey.SA_NUX$FOOTER_TERMS,
    href: TERMS_OF_SERVICE_URL,
  },
  {
    label: I18nKey.SA_NUX$FOOTER_PRIVACY,
    href: PRIVACY_POLICY_URL,
  },
  {
    label: I18nKey.SA_NUX$FOOTER_DOCS,
    href: "https://docs.openhands.dev",
  },
  {
    label: I18nKey.SA_NUX$FOOTER_GITHUB,
    href: "https://github.com/OpenHands/OpenHands",
  },
  {
    label: I18nKey.SA_NUX$FOOTER_SUPPORT,
    href: "https://docs.openhands.dev/enterprise/troubleshooting",
  },
] as const;

/**
 * Bottom rule, copyright, and legal links. Matches the customer-center footer.
 */
export function InstallFooter() {
  const { t } = useTranslation();

  return (
    <footer
      data-testid="install-footer"
      className="w-full shrink-0 border-t border-[var(--oh-border)] px-6 py-6 text-xs leading-5 !text-[#979797]"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        <p>{t(I18nKey.SA_NUX$COPYRIGHT, { year: new Date().getFullYear() })}</p>
        <nav
          aria-label={t(I18nKey.SA_NUX$FOOTER_LEGAL)}
          className="flex flex-wrap items-center gap-x-2 gap-y-1"
        >
          {FOOTER_LINKS.map((link, index) => (
            <span key={link.href} className="inline-flex items-center gap-x-2">
              {index > 0 ? <span aria-hidden>|</span> : null}
              <a
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
                className="!text-[#979797] hover:!text-white"
              >
                {t(link.label)}
              </a>
            </span>
          ))}
        </nav>
      </div>
    </footer>
  );
}
