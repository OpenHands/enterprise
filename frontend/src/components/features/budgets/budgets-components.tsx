/* eslint-disable i18next/no-literal-string */
import React from "react";
import { ToggleSwitch } from "#/ui/toggle-switch";
import { cn } from "#/utils/utils";

export function Toggle({
  enabled,
  onChange,
  label,
}: {
  enabled: boolean;
  onChange: (value: boolean) => void;
  label: string;
}) {
  return (
    <ToggleSwitch
      enabled={enabled}
      label={label}
      onToggle={() => onChange(!enabled)}
    />
  );
}

export function PillBadge({
  active,
  icon,
  label,
  disabled = false,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  disabled?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
        active
          ? "bg-primary/10 text-primary border-primary/30"
          : "bg-base-secondary text-[var(--oh-muted)] border-[var(--oh-border)]",
        disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer",
      )}
    >
      {active && <span className="text-primary">✓</span>}
      {icon}
      {label}
    </span>
  );
}

export function SpendMeter({
  percentage,
  showTicks = true,
}: {
  percentage: number;
  showTicks?: boolean;
}) {
  const getBarColor = () => {
    if (percentage >= 90)
      return "bg-gradient-to-r from-success via-logo to-danger";
    if (percentage >= 80)
      return "bg-gradient-to-r from-success via-logo to-logo";
    return "bg-gradient-to-r from-success to-logo";
  };

  return (
    <div className="w-full">
      <div className="relative w-full h-3 bg-tertiary rounded-full overflow-hidden">
        <div
          className={`absolute inset-y-0 left-0 rounded-full ${getBarColor()}`}
          style={{ width: `${Math.min(percentage, 100)}%` }}
        />
      </div>
      {showTicks && (
        <div className="relative mt-1">
          <div className="flex justify-between text-[10px] text-[var(--oh-muted)]">
            <span>0%</span>
            <span>80%</span>
            <span>90%</span>
            <span>100%</span>
          </div>
          <div className="absolute top-0 left-[80%] w-px h-2 bg-[var(--oh-muted)]" />
          <div className="absolute top-0 left-[90%] w-px h-2 bg-[var(--oh-muted)]" />
          <div className="absolute top-0 left-[100%] w-px h-2 bg-[var(--oh-muted)]" />
        </div>
      )}
    </div>
  );
}

export function UserProgressBar({
  value,
  max,
  status,
}: {
  value: number;
  max: number;
  status: "green" | "yellow" | "red";
}) {
  const percentage = max > 0 ? (value / max) * 100 : 0;
  const colorClass = {
    red: "bg-danger",
    yellow: "bg-logo",
    green: "bg-success",
  }[status];

  return (
    <div className="w-full">
      <div className="relative w-full h-1.5 bg-tertiary rounded-full overflow-hidden">
        <div
          className={`absolute inset-y-0 left-0 rounded-full ${colorClass}`}
          style={{ width: `${Math.min(percentage, 100)}%` }}
        />
      </div>
      <div className="text-xs text-muted mt-1">
        ${value.toLocaleString()} / ${max.toLocaleString()}
      </div>
    </div>
  );
}

export function Avatar({
  name,
  size = "md",
}: {
  name: string;
  size?: "sm" | "md";
}) {
  const initials = name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  const sizeClass = size === "sm" ? "w-7 h-7 text-xs" : "w-9 h-9 text-sm";

  return (
    <div
      className={`${sizeClass} rounded-full bg-tertiary text-foreground flex items-center justify-center font-medium`}
    >
      {initials}
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const getStyle = () => {
    if (status.includes("Over cap")) {
      return "bg-danger/20 text-danger border-danger/30";
    }
    if (status.includes("> 90%")) {
      return "bg-danger/10 text-danger border-danger/30";
    }
    if (status.includes("> 80%")) {
      return "bg-logo/10 text-logo border-logo/30";
    }
    if (status.includes("On track")) {
      return "bg-success/10 text-success border-success/30";
    }
    return "bg-base-secondary text-[var(--oh-muted)] border-[var(--oh-border)]";
  };

  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${getStyle()}`}
    >
      {status}
    </span>
  );
}
