import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListRowHeightClassName,
} from "#/utils/settings-list-classes";

export function SettingsListSkeleton() {
  const { t } = useTranslation();

  return (
    <div
      role="status"
      aria-label={t(I18nKey.HOME$LOADING)}
      className={cn(
        settingsListContainerClassName,
        settingsListDividerClassName,
      )}
    >
      {[0, 1].map((row) => (
        <div
          key={row}
          aria-hidden="true"
          className={cn(
            settingsListRowHeightClassName,
            "flex items-center gap-4 px-3",
          )}
        >
          <div className="h-3 w-24 max-w-full skeleton" />
          <div className="h-3 w-40 max-w-full skeleton" />
        </div>
      ))}
    </div>
  );
}
