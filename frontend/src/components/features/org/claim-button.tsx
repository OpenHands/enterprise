import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import {
  formControlRadiusClassName,
  formControlTransitionClassName,
} from "#/utils/form-control-classes";
import type { GitOrg } from "#/types/org";

type ButtonState =
  | "claiming"
  | "disconnecting"
  | "disconnect"
  | "claimed"
  | "unclaimed";

const BUTTON_STYLES: Record<ButtonState, string> = {
  claiming:
    "border border-[var(--oh-border)] bg-base text-white opacity-50 cursor-not-allowed flex items-center justify-center",
  disconnecting:
    "border border-[var(--oh-border)] bg-base text-white opacity-50 cursor-not-allowed",
  disconnect:
    "border border-red-500/60 bg-red-500/15 text-[var(--oh-status-error)] font-medium cursor-pointer",
  claimed:
    "border border-emerald-500/60 bg-emerald-500/20 text-[var(--oh-status-success)] font-medium cursor-pointer flex items-center justify-center",
  unclaimed:
    "border border-[var(--oh-border)] bg-base text-white cursor-pointer flex items-center justify-center",
};

const BUTTON_HOVER_STYLES: Partial<Record<ButtonState, string>> = {
  unclaimed: "bg-[var(--oh-interactive-hover)]",
};

const BUTTON_LABELS: Record<ButtonState, I18nKey> = {
  claiming: I18nKey.ORG$CLAIM,
  disconnecting: I18nKey.ORG$DISCONNECT,
  disconnect: I18nKey.ORG$DISCONNECT,
  claimed: I18nKey.ORG$CLAIMED,
  unclaimed: I18nKey.ORG$CLAIM,
};

export function getButtonState(
  status: GitOrg["status"],
  isHovered: boolean,
): ButtonState {
  if (status === "claiming" || status === "disconnecting") return status;
  if (status === "claimed" && isHovered) return "disconnect";
  return status;
}

interface ClaimButtonProps {
  org: GitOrg;
  onClaim: (id: string) => void;
  onDisconnect: (id: string) => void;
}

export function ClaimButton({ org, onClaim, onDisconnect }: ClaimButtonProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = React.useState(false);

  const buttonState = getButtonState(org.status, isHovered);
  const isDisabled =
    org.status === "claiming" || org.status === "disconnecting";

  const handleClick = () => {
    if (org.status === "unclaimed") onClaim(org.id);
    if (org.status === "claimed") onDisconnect(org.id);
  };

  return (
    <button
      type="button"
      data-testid={`claim-button-${org.id}`}
      onClick={handleClick}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      disabled={isDisabled}
      className={cn(
        "h-7 px-3 text-xs leading-4 text-center whitespace-nowrap",
        formControlRadiusClassName,
        formControlTransitionClassName,
        BUTTON_STYLES[buttonState],
        isHovered && BUTTON_HOVER_STYLES[buttonState],
      )}
    >
      {t(BUTTON_LABELS[buttonState])}
    </button>
  );
}
