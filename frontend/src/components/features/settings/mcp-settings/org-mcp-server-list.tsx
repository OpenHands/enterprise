import { Settings } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { MCPServerIcon } from "#/components/features/settings/mcp-settings/mcp-server-icon";
import { mcpServerDisplayName } from "#/components/features/settings/mcp-settings/mcp-server-list-item";
import { I18nKey } from "#/i18n/declaration";
import { Text } from "#/ui/typography";
import {
  OrgMcpServerView,
  isOrgMcpServerConnected,
} from "#/utils/org-mcp-servers";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListRowActionButtonClassName,
  settingsListSectionHeaderClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";
import DeleteIcon from "#/icons/u-delete.svg?react";

interface OrgMcpServerListItemProps {
  listing: OrgMcpServerView;
  isConnected: boolean;
  canManage: boolean;
  /** This row's OAuth flow is running or being saved. */
  isConnecting: boolean;
  onEdit: () => void;
  onDelete: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
}

function OrgMcpServerListItem({
  listing,
  isConnected,
  canManage,
  isConnecting,
  onEdit,
  onDelete,
  onConnect,
  onDisconnect,
}: OrgMcpServerListItemProps) {
  const { t } = useTranslation();
  const displayName = mcpServerDisplayName(listing.name);
  const typeLabel =
    listing.type === "sse"
      ? t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SSE)
      : t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SHTTP);
  const isUserManaged = listing.managedBy === "user";
  const isOAuth = listing.auth === "oauth";
  const needsConnectionMessage = isOAuth
    ? t(I18nKey.SETTINGS$MCP_ORG_OAUTH_NEEDS_CONNECTION)
    : t(I18nKey.SETTINGS$MCP_ORG_NEEDS_CONNECTION, {
        names: listing.secretNames.join(", "),
      });

  return (
    <div
      data-testid="org-mcp-server-item"
      data-managed-by={listing.managedBy}
      className="flex items-center justify-between gap-4 px-3 py-3"
    >
      <div className="flex min-w-0 items-center gap-3">
        <MCPServerIcon server={{ name: listing.name, url: listing.url }} />
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Text
              className="truncate text-sm font-medium leading-5 text-content-2"
              title={listing.name}
            >
              {displayName}
            </Text>
            <span className="inline-flex shrink-0 items-center rounded-md bg-white/10 px-2 py-0.5 text-xs font-medium whitespace-nowrap text-[var(--oh-muted)]">
              {typeLabel}
            </span>
            <span
              data-testid="org-mcp-server-badge"
              className={cn(
                "inline-flex shrink-0 items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
                isUserManaged
                  ? "bg-success/15 text-success"
                  : "bg-blue-500/15 text-blue-300",
              )}
            >
              {isUserManaged
                ? t(I18nKey.SETTINGS$MCP_USER_MANAGED_BADGE)
                : t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BADGE)}
            </span>
          </div>
          <Text
            className="truncate text-xs leading-4 text-[var(--oh-muted)]"
            title={listing.url}
          >
            {listing.url}
          </Text>
          {isUserManaged ? (
            <Text
              testId="org-mcp-server-status"
              className={cn(
                "text-xs leading-4",
                isConnected ? "text-success" : "text-amber-300",
              )}
            >
              {isConnected
                ? t(I18nKey.SETTINGS$MCP_ORG_CONNECTED)
                : needsConnectionMessage}
            </Text>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {isUserManaged && !isConnected ? (
          <BrandButton
            testId="connect-org-mcp-server-button"
            type="button"
            variant="primary"
            onClick={onConnect}
            isDisabled={isConnecting}
            className={settingsListRowActionButtonClassName}
          >
            {isConnecting
              ? t(I18nKey.SETTINGS$MCP_ORG_OAUTH_AUTHORIZING)
              : t(I18nKey.SETTINGS$MCP_ORG_CONNECT)}
          </BrandButton>
        ) : null}
        {isUserManaged && isOAuth && isConnected ? (
          <BrandButton
            testId="disconnect-org-mcp-server-button"
            type="button"
            variant="secondary"
            onClick={onDisconnect}
            className={settingsListRowActionButtonClassName}
          >
            {t(I18nKey.SETTINGS$MCP_ORG_DISCONNECT)}
          </BrandButton>
        ) : null}
        {canManage ? (
          <>
            <BrandButton
              testId="edit-org-mcp-server-button"
              type="button"
              variant="secondary"
              onClick={onEdit}
              className={settingsListRowActionButtonClassName}
              startContent={
                <Settings className="size-3.5 shrink-0" aria-hidden />
              }
            >
              {t(I18nKey.BUTTON$EDIT)}
            </BrandButton>
            <button
              data-testid="delete-org-mcp-server-button"
              type="button"
              onClick={onDelete}
              aria-label={`Delete ${displayName}`}
              className="inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted hover:bg-[var(--oh-interactive-hover-low)] hover:text-white"
            >
              <DeleteIcon width={16} height={16} />
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}

interface OrgMcpServerListProps {
  listings: OrgMcpServerView[];
  /** Names of the secrets this member can use; decides "connected". */
  availableSecretNames: ReadonlySet<string>;
  canManage: boolean;
  /** The listing whose OAuth flow is running, if any. */
  connectingName?: string | null;
  onEdit: (listing: OrgMcpServerView) => void;
  onDelete: (listing: OrgMcpServerView) => void;
  onConnect: (listing: OrgMcpServerView) => void;
  onDisconnect: (listing: OrgMcpServerView) => void;
}

/**
 * The organization's MCP server listings in two groups: the ones the
 * organization connects for everyone, and the ones each member connects
 * with their own secret or account (OHE-3091).
 */
export function OrgMcpServerList({
  listings,
  availableSecretNames,
  canManage,
  connectingName = null,
  onEdit,
  onDelete,
  onConnect,
  onDisconnect,
}: OrgMcpServerListProps) {
  const { t } = useTranslation();
  const groups = [
    {
      key: "org" as const,
      title: t(I18nKey.SETTINGS$MCP_ORG_MANAGED_GROUP),
      items: listings.filter((listing) => listing.managedBy === "org"),
    },
    {
      key: "user" as const,
      title: t(I18nKey.SETTINGS$MCP_USER_MANAGED_GROUP),
      items: listings.filter((listing) => listing.managedBy === "user"),
    },
  ].filter((group) => group.items.length > 0);

  if (groups.length === 0) {
    return (
      <div className={cn(settingsListContainerClassName, "p-8 text-center")}>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SETTINGS$MCP_ORG_NONE)}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4" data-testid="org-mcp-server-list">
      {groups.map((group) => (
        <div
          key={group.key}
          data-testid={`org-mcp-server-group-${group.key}`}
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
          )}
        >
          <div className={settingsListSectionHeaderClassName}>
            {group.title}
          </div>
          {group.items.map((listing) => (
            <OrgMcpServerListItem
              key={listing.name}
              listing={listing}
              isConnected={isOrgMcpServerConnected(
                listing,
                availableSecretNames,
              )}
              canManage={canManage}
              isConnecting={connectingName === listing.name}
              onEdit={() => onEdit(listing)}
              onDelete={() => onDelete(listing)}
              onConnect={() => onConnect(listing)}
              onDisconnect={() => onDisconnect(listing)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
