import { NavLink } from "react-router";
import { useTranslation } from "react-i18next";
import OpenHandsLogo from "#/assets/branding/openhands-logo.svg?react";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { I18nKey } from "#/i18n/declaration";
import { StyledTooltip } from "#/components/shared/buttons/styled-tooltip";
import { cn } from "#/utils/utils";

interface OpenHandsLogoButtonProps {
  /** Colored mark (default) or white mark for dark surfaces. */
  variant?: "color" | "white";
  /** Show the OpenHands wordmark text beside the mark. */
  showText?: boolean;
  logoWidth?: number;
  logoHeight?: number;
  className?: string;
}

export function OpenHandsLogoButton({
  variant = "color",
  showText = false,
  logoWidth,
  logoHeight,
  className,
}: OpenHandsLogoButtonProps) {
  const { t } = useTranslation();

  const tooltipText = t(I18nKey.BRANDING$OPENHANDS);
  const ariaLabel = t(I18nKey.BRANDING$OPENHANDS_LOGO);
  const Logo = variant === "white" ? OpenHandsLogoWhite : OpenHandsLogo;
  const width = logoWidth ?? (variant === "white" ? 34 : 46);
  const height = logoHeight ?? (variant === "white" ? 34 : 30);

  const mark = (
    <NavLink
      to="/"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-2.5 min-w-0",
        showText && "hover:opacity-90 transition-opacity",
        className,
      )}
    >
      <Logo width={width} height={height} className="shrink-0" />
      {showText && (
        <span className="truncate text-sm font-medium text-white">
          {t(I18nKey.BRANDING$OPENHANDS)}
        </span>
      )}
    </NavLink>
  );

  // Text label already conveys the brand — skip the tooltip in that case.
  if (showText) {
    return mark;
  }

  return <StyledTooltip content={tooltipText}>{mark}</StyledTooltip>;
}
