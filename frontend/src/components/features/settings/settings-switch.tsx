import React from "react";
import { useTranslation } from "react-i18next";
import { InfoTooltip } from "#/components/features/settings/info-tooltip";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { ToggleSwitchVisual } from "#/ui/toggle-switch";

interface SettingsSwitchProps {
  testId?: string;
  name?: string;
  onToggle?: (value: boolean) => void;
  defaultIsToggled?: boolean;
  isToggled?: boolean;
  isBeta?: boolean;
  isDisabled?: boolean;
  /** Whether the toggle sits before or after the label. Defaults to "left". */
  togglePosition?: "left" | "right";
  /** Explanatory text revealed by an info icon next to the label. Use this
   * instead of a separate hint paragraph when the label alone is ambiguous. */
  hint?: string;
}

export function SettingsSwitch({
  children,
  testId,
  name,
  onToggle,
  defaultIsToggled,
  isToggled: controlledIsToggled,
  isBeta,
  isDisabled,
  togglePosition = "left",
  hint,
}: React.PropsWithChildren<SettingsSwitchProps>) {
  const { t } = useTranslation();
  const [isToggled, setIsToggled] = React.useState(defaultIsToggled ?? false);

  const handleToggle = (value: boolean) => {
    if (isDisabled) return;
    setIsToggled(value);
    onToggle?.(value);
  };

  const input = (
    <input
      hidden
      data-testid={testId}
      name={name}
      type="checkbox"
      onChange={(e) => handleToggle(e.target.checked)}
      checked={controlledIsToggled ?? isToggled}
      disabled={isDisabled}
    />
  );

  const toggle = (
    <ToggleSwitchVisual enabled={controlledIsToggled ?? isToggled} />
  );

  const label =
    children || isBeta ? (
      <div className="flex items-center gap-1">
        <span className="text-sm">{children}</span>
        {isBeta && (
          <span className="text-[11px] leading-4 text-[var(--oh-color-base)] font-[500] tracking-tighter bg-primary px-1 rounded-full">
            {t(I18nKey.BADGE$BETA)}
          </span>
        )}
        {hint ? (
          // The switch lives inside a <label>, so a click anywhere in here
          // would otherwise toggle it -- the hint must not.
          <span
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
            }}
          >
            <InfoTooltip content={hint} />
          </span>
        ) : null}
      </div>
    ) : null;

  return (
    <label
      className={cn(
        "flex items-center gap-2",
        togglePosition === "right" ? "w-full justify-between" : "w-fit",
        isDisabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
      )}
    >
      {input}
      {togglePosition === "right" ? (
        <>
          {label}
          {toggle}
        </>
      ) : (
        <>
          {toggle}
          {label}
        </>
      )}
    </label>
  );
}
