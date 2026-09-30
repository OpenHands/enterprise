import { useTranslation } from "react-i18next";
import { MCPServerListItem } from "./mcp-server-list-item";
import { HorizontalScrollFade } from "#/components/shared/horizontal-scroll-fade";
import { I18nKey } from "#/i18n/declaration";
import {
  settingsListContainerClassName,
  settingsListScrollFadeFromClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

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

interface MCPServerListProps {
  servers: MCPServerConfig[];
  onEdit: (server: MCPServerConfig) => void;
  onDelete: (serverId: string) => void;
}

export function MCPServerList({
  servers,
  onEdit,
  onDelete,
}: MCPServerListProps) {
  const { t } = useTranslation();

  if (servers.length === 0) {
    return (
      <div className={cn(settingsListContainerClassName, "p-8 text-center")}>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SETTINGS$MCP_NO_SERVERS)}
        </p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        settingsListContainerClassName,
        settingsListScrollFadeFromClassName,
      )}
    >
      <HorizontalScrollFade>
        <table className="w-full table-fixed" style={{ minWidth: "27.5rem" }}>
          <thead className={settingsListTableHeadClassName}>
            <tr>
              <th className={settingsListTableHeaderCellClassName}>
                {t(I18nKey.SETTINGS$NAME)}
              </th>
              <th
                className={cn(
                  settingsListTableHeaderCellClassName,
                  "w-[7.5rem]",
                )}
              >
                {t(I18nKey.SETTINGS$MCP_SERVER_TYPE)}
              </th>
              <th
                className={cn(settingsListTableHeaderCellClassName, "min-w-36")}
              >
                {t(I18nKey.SETTINGS$MCP_SERVER_DETAILS)}
              </th>
              <th
                className={cn(
                  settingsListTableHeaderCellClassName,
                  "w-20 text-right",
                )}
              >
                {t(I18nKey.SETTINGS$ACTIONS)}
              </th>
            </tr>
          </thead>
          <tbody>
            {servers.map((server) => (
              <MCPServerListItem
                key={server.id}
                server={server}
                onEdit={() => onEdit(server)}
                onDelete={() => onDelete(server.id)}
              />
            ))}
          </tbody>
        </table>
      </HorizontalScrollFade>
    </div>
  );
}
