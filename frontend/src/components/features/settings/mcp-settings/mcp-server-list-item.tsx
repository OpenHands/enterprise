import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import {
  settingsListIconActionButtonClassName,
  settingsListTableCellClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";
import EditIcon from "#/icons/u-edit.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";

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
      return serverConfig.name || "";
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
  const serverDescription = getServerDescription(server);

  return (
    <tr data-testid="mcp-server-item" className={settingsListTableRowClassName}>
      <td
        className={cn(
          settingsListTableCellClassName,
          "truncate text-content-2",
        )}
        title={serverName}
      >
        {serverName}
      </td>

      <td
        className={cn(
          settingsListTableCellClassName,
          "whitespace-nowrap text-content-2",
        )}
      >
        {getServerTypeLabel(server.type)}
      </td>

      <td
        className={cn(
          settingsListTableCellClassName,
          "truncate text-content-2 opacity-80",
        )}
        title={serverDescription}
      >
        {serverDescription}
      </td>

      <td className={cn(settingsListTableCellClassName, "text-right")}>
        <div className="flex items-center justify-end gap-0.5">
          <button
            data-testid="edit-mcp-server-button"
            type="button"
            onClick={onEdit}
            aria-label={`Edit ${serverName}`}
            className={settingsListIconActionButtonClassName}
          >
            <EditIcon width={16} height={16} />
          </button>
          <button
            data-testid="delete-mcp-server-button"
            type="button"
            onClick={onDelete}
            aria-label={`Delete ${serverName}`}
            className={settingsListIconActionButtonClassName}
          >
            <DeleteIcon width={16} height={16} />
          </button>
        </div>
      </td>
    </tr>
  );
}
