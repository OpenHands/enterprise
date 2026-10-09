import type { KeyboardEvent } from "react";
import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { MCPServerIcon } from "#/components/features/settings/mcp-settings/mcp-server-icon";
import { I18nKey } from "#/i18n/declaration";
import { Text } from "#/ui/typography";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import {
  settingsListRowActionButtonClassName,
  settingsListRowHoverClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";
import DeleteIcon from "#/icons/u-delete.svg?react";

const BRANDED_SERVER_NAMES: Record<string, string> = {
  github: "GitHub",
  gitlab: "GitLab",
};

/** Visible label only. The stored name stays the config key. */
export function mcpServerDisplayName(name: string): string {
  const trimmed = name.trim();
  if (!trimmed || /^https?:\/\//i.test(trimmed)) {
    return trimmed;
  }
  const branded = BRANDED_SERVER_NAMES[trimmed.toLowerCase()];
  if (branded) {
    return branded;
  }
  return trimmed
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

interface MCPServerConfig {
  id: string;
  type: "sse" | "stdio" | "shttp";
  name?: string;
  url?: string;
  api_key?: string;
  timeout?: number;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}

export function MCPServerListItem({
  server,
  onEdit,
  onDelete,
}: {
  server: MCPServerConfig;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();

  const getServerTypeLabel = (type: string) => {
    switch (type) {
      case "sse":
        return t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SSE);
      case "stdio":
        return t(I18nKey.SETTINGS$MCP_SERVER_TYPE_STDIO);
      case "shttp":
        return t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SHTTP);
      default:
        return type.toUpperCase();
    }
  };

  const getServerDescription = (serverConfig: MCPServerConfig) => {
    if (serverConfig.type === "stdio") {
      if (serverConfig.command) {
        const args =
          serverConfig.args && serverConfig.args.length > 0
            ? ` ${serverConfig.args.join(" ")}`
            : "";
        return `${serverConfig.command}${args}`;
      }
      return "";
    }
    if (
      (serverConfig.type === "sse" || serverConfig.type === "shttp") &&
      serverConfig.url
    ) {
      return serverConfig.url;
    }
    return "";
  };

  const serverName = server.name || server.url || "";
  const displayName = mcpServerDisplayName(serverName);
  const serverDescription = getServerDescription(server);
  const typeLabel = getServerTypeLabel(server.type);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onEdit();
    }
  };

  return (
    <div
      role="button"
      tabIndex={0}
      data-testid="mcp-server-item"
      onClick={onEdit}
      onKeyDown={onKeyDown}
      className={cn(
        "flex cursor-pointer items-center justify-between gap-4 px-3 py-3",
        formControlTransitionClassName,
        settingsListRowHoverClassName,
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <MCPServerIcon server={server} />
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex min-w-0 items-center gap-2">
            <Text
              className="truncate text-sm font-medium leading-5 text-content-2"
              title={serverName}
            >
              {displayName}
            </Text>
            <span className="inline-flex shrink-0 items-center rounded-md bg-white/10 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-[var(--oh-muted)]">
              {typeLabel}
            </span>
          </div>
          {serverDescription ? (
            <Text
              className="truncate text-xs leading-4 text-[var(--oh-muted)]"
              title={serverDescription}
            >
              {serverDescription}
            </Text>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <BrandButton
          testId="edit-mcp-server-button"
          type="button"
          variant="secondary"
          onClick={(event) => {
            event?.stopPropagation();
            onEdit();
          }}
          className={settingsListRowActionButtonClassName}
          startContent={<Settings className="size-3.5 shrink-0" aria-hidden />}
        >
          {t(I18nKey.BUTTON$EDIT)}
        </BrandButton>
        <button
          data-testid="delete-mcp-server-button"
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onDelete();
          }}
          aria-label={`Delete ${displayName}`}
          className="inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted hover:bg-[var(--oh-interactive-hover-low)] hover:text-white"
        >
          <DeleteIcon width={16} height={16} />
        </button>
      </div>
    </div>
  );
}
