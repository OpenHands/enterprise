import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

interface SettingsNavHeaderProps {
  text: I18nKey;
  className?: string;
}

export function SettingsNavHeader({ text, className }: SettingsNavHeaderProps) {
  const { t } = useTranslation();

  return (
    <div className={cn("px-2.5 pt-3 first:pt-0.5", className)}>
      <p className="text-[11px] font-medium uppercase tracking-wide leading-5 text-[var(--oh-muted)]">
        {t(text)}
      </p>
    </div>
  );
}
