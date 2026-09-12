import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

interface SettingsNavHeaderProps {
  text: I18nKey;
  chip?: I18nKey;
  className?: string;
}

export function SettingsNavHeader({
  text,
  chip,
  className,
}: SettingsNavHeaderProps) {
  const { t } = useTranslation();

  return (
    <div
      className={cn(
        "flex items-center gap-1.5 px-2.5 pt-3 first:pt-0.5",
        className,
      )}
    >
      <p className="text-[11px] font-medium uppercase tracking-wide leading-5 text-[var(--oh-muted)]">
        {t(text)}
      </p>
      {chip && (
        <span
          data-testid="settings-nav-header-chip"
          className="inline-flex items-center rounded-full border border-[var(--oh-border)] bg-base-secondary px-1.5 py-0.5 text-[8px] font-medium leading-none whitespace-nowrap text-[var(--oh-muted)]"
        >
          {t(chip)}
        </span>
      )}
    </div>
  );
}
