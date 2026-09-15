import type { ReactNode } from "react";
import { cn } from "#/utils/utils";

interface HubBadgeProps {
  children: ReactNode;
  tone?: "default" | "success" | "warning";
  size?: "default" | "sm";
}

export function HubBadge({
  children,
  tone = "default",
  size = "default",
}: HubBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex rounded-md font-medium tracking-[0.01em]",
        size === "sm"
          ? "px-1.5 py-0.5 text-[10px] leading-4"
          : "px-2.5 py-1 text-[11px]",
        tone === "success" &&
          "border border-[color:rgba(165,231,94,0.25)] bg-[color:rgba(165,231,94,0.12)] text-[var(--oh-color-success)]",
        tone === "warning" &&
          "border border-[color:rgba(217,181,90,0.25)] bg-[color:rgba(217,181,90,0.12)] text-[var(--oh-warning)]",
        tone === "default" &&
          "border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] text-foreground",
      )}
    >
      {children}
    </span>
  );
}
