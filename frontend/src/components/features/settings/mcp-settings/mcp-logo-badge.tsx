import React from "react";
import { Bot } from "lucide-react";
import SlackIcon from "#/icons/slack.svg?react";
import type { McpLibraryEntry } from "#/utils/mcp-library";
import { cn } from "#/utils/utils";

// Slack's remote catalog logo (cdn.simpleicons.org) no longer resolves, so it
// uses the bundled mark, as the agent-canvas MCP page does.
const LOCAL_LOGO_ICONS: Record<
  string,
  React.ComponentType<React.SVGProps<SVGSVGElement>>
> = {
  slack: SlackIcon,
};

interface McpLogoBadgeProps {
  entry: Pick<
    McpLibraryEntry,
    "id" | "name" | "iconBg" | "iconColor" | "logoUrl"
  >;
}

/**
 * A catalog entry's logo. Entries without one, and logos that cannot load
 * (e.g. an install without internet access to the logo CDN), show a generic
 * mark instead.
 */
export function McpLogoBadge({ entry }: McpLogoBadgeProps) {
  const [logoFailed, setLogoFailed] = React.useState(false);
  const LocalLogoIcon = LOCAL_LOGO_ICONS[entry.id];

  let logo: React.ReactNode = <Bot className="h-5 w-5" strokeWidth={2.25} />;
  if (LocalLogoIcon) {
    logo = <LocalLogoIcon className="h-5 w-5" />;
  } else if (entry.logoUrl && !logoFailed) {
    logo = (
      <img
        src={entry.logoUrl}
        alt=""
        className="h-full w-full object-contain p-[22%]"
        onError={() => setLogoFailed(true)}
      />
    );
  }

  return (
    <span
      aria-hidden="true"
      title={entry.name}
      className={cn(
        "inline-flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg",
        "border border-[var(--oh-border)]",
      )}
      style={{
        backgroundColor: entry.iconBg ?? "var(--oh-color-tertiary)",
        color:
          entry.iconColor ??
          (entry.iconBg ? "#FFFFFF" : "var(--oh-foreground)"),
      }}
    >
      {logo}
    </span>
  );
}
