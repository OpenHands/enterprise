import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";

interface IntegrationsHubPageHeaderProps {
  title: I18nKey;
  subtitle: I18nKey;
  actions?: ReactNode;
  subtitleExtra?: ReactNode;
}

export function IntegrationsHubPageHeader({
  title,
  subtitle,
  actions,
  subtitleExtra,
}: IntegrationsHubPageHeaderProps) {
  const { t } = useTranslation();

  return (
    <header className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
      <div className="min-w-0 space-y-1">
        <h1 className="text-xl font-medium leading-6 tracking-[-0.02em] text-white">
          {t(title)}
        </h1>
        <p
          data-testid="settings-page-subtitle"
          className="text-sm leading-5 text-tertiary-light"
        >
          {t(subtitle)}
          {subtitleExtra}
        </p>
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </header>
  );
}
