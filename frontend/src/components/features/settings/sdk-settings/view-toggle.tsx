import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { SettingsView } from "#/utils/sdk-settings-schema";
import { cn } from "#/utils/utils";
import { formControlTransitionClassName } from "#/utils/form-control-classes";

interface ViewToggleProps {
  view: SettingsView;
  setView: (view: SettingsView) => void;
  /** Whether the basic tier has anything to show (any critical fields). */
  showBasic?: boolean;
  showAdvanced: boolean;
  showAll: boolean;
  isDisabled?: boolean;
  // Extra buttons rendered in the same flex row as Basic/Advanced/All,
  // placed after them. Used to slot section-level nav (e.g. a Profiles
  // button) into the same control strip as the view toggles.
  trailing?: React.ReactNode;
}

const tabButtonClass = (isActive: boolean, isDisabled: boolean) =>
  cn(
    "w-fit px-2 py-2 text-sm cursor-pointer rounded-none bg-transparent",
    formControlTransitionClassName,
    "border-b-2 pb-2",
    isActive
      ? "text-white border-white"
      : "text-[var(--oh-muted)] border-transparent hover:text-white",
    isDisabled && "pointer-events-none opacity-30 cursor-not-allowed",
  );

export function ViewToggle({
  view,
  setView,
  showBasic = true,
  showAdvanced,
  showAll,
  isDisabled = false,
  trailing,
}: ViewToggleProps) {
  const { t } = useTranslation();

  const visibleTabs = [showBasic, showAdvanced, showAll].filter(Boolean).length;
  const hasViewButtons = visibleTabs > 1;
  if (!hasViewButtons && !trailing) return null;

  return (
    <div className="mb-6 flex items-center gap-2 flex-wrap">
      {hasViewButtons ? (
        <div
          role="tablist"
          aria-orientation="horizontal"
          className="flex items-center gap-2"
        >
          {showBasic ? (
            <button
              data-testid="sdk-section-basic-toggle"
              type="button"
              role="tab"
              aria-selected={view === "basic"}
              disabled={isDisabled}
              className={tabButtonClass(view === "basic", isDisabled)}
              onClick={() => setView("basic")}
            >
              {t(I18nKey.SETTINGS$BASIC)}
            </button>
          ) : null}
          {showAdvanced ? (
            <button
              data-testid="sdk-section-advanced-toggle"
              type="button"
              role="tab"
              aria-selected={view === "advanced"}
              disabled={isDisabled}
              className={tabButtonClass(view === "advanced", isDisabled)}
              onClick={() => setView("advanced")}
            >
              {t(I18nKey.SETTINGS$ADVANCED)}
            </button>
          ) : null}
          {showAll ? (
            <button
              data-testid="sdk-section-all-toggle"
              type="button"
              role="tab"
              aria-selected={view === "all"}
              disabled={isDisabled}
              className={tabButtonClass(view === "all", isDisabled)}
              onClick={() => setView("all")}
            >
              {t(I18nKey.SETTINGS$ALL)}
            </button>
          ) : null}
        </div>
      ) : null}
      {trailing}
    </div>
  );
}
