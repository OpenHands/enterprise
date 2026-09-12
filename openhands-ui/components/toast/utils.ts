import type { IconProps } from "../icon/Icon";

type ToastType = "error" | "success" | "info" | "warning";

/**
 * Icon + color pairing aligned with agent-canvas / OpenHands-Neo status tokens:
 * error → #FF684E, success → green status, warning → primary gold.
 */
export const toastStyles: Record<
  ToastType,
  { icon: IconProps["icon"]; iconColor: string; textColor: string }
> = {
  error: {
    icon: "XCircle",
    iconColor: "text-red-500",
    textColor: "text-light-neutral-600",
  },
  success: {
    icon: "CheckCircle",
    iconColor: "text-green-600",
    textColor: "text-white",
  },
  info: {
    icon: "InfoCircle",
    iconColor: "text-aqua-600",
    textColor: "text-white",
  },
  warning: {
    icon: "ExclamationTriangle",
    iconColor: "text-primary-500",
    textColor: "text-white",
  },
};
