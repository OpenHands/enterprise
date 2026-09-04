import React, { useEffect, useState } from "react";
import { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { useSettings } from "#/hooks/query/use-settings";
import { useConfig } from "#/hooks/query/use-config";
import { useSaveSettings } from "#/hooks/mutation/use-save-settings";
import { useDeleteMcpServer } from "#/hooks/mutation/use-delete-mcp-server";
import { useAddMcpServer } from "#/hooks/mutation/use-add-mcp-server";
import { useUpdateMcpServer } from "#/hooks/mutation/use-update-mcp-server";
import { I18nKey } from "#/i18n/declaration";

import { MCPServerList } from "#/components/features/settings/mcp-settings/mcp-server-list";
import { MCPServerModal } from "#/components/features/settings/mcp-settings/mcp-server-modal";
import { KeyStatusIcon } from "#/components/features/settings/key-status-icon";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { HelpLink } from "#/ui/help-link";
import { MCPConfig } from "#/types/settings";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { parseMcpConfig } from "#/utils/mcp-config";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { Typography } from "#/ui/typography";

export const clientLoader = createPermissionGuard("manage_mcp");
export const handle = { hideTitle: true };

type MCPServerType = "sse" | "stdio" | "shttp";

interface MCPServerConfig {
  id: string;
  type: MCPServerType;
  name?: string;
  url?: string;
  api_key?: string;
  timeout?: number;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}

function MCPSettingsScreen() {
  const { t } = useTranslation();
  const { data: settings, isLoading } = useSettings();
  const { data: config } = useConfig();
  const { mutate: saveSettings, isPending: isSavingSearchApiKey } =
    useSaveSettings();

  const { mutate: deleteMcpServer } = useDeleteMcpServer();
  const { mutate: addMcpServer } = useAddMcpServer();
  const { mutate: updateMcpServer } = useUpdateMcpServer();

  const [view, setView] = useState<"list" | "add" | "edit">("list");
  const [editingServer, setEditingServer] = useState<MCPServerConfig | null>(
    null,
  );
  const [searchApiKey, setSearchApiKey] = useState("");
  const [searchApiKeyDirty, setSearchApiKeyDirty] = useState(false);
  const [confirmationModalIsVisible, setConfirmationModalIsVisible] =
    useState(false);
  const [serverToDelete, setServerToDelete] = useState<string | null>(null);

  const isSaasMode = config?.app_mode === "saas";

  const mcpConfig: MCPConfig = parseMcpConfig(
    settings?.agent_settings?.mcp_config,
  );

  const allServers: MCPServerConfig[] = [
    ...mcpConfig.sse_servers.map((server, index) => ({
      id: `sse-${index}`,
      type: "sse" as const,
      name: typeof server === "object" ? server.name : undefined,
      url: typeof server === "string" ? server : server.url,
      api_key: typeof server === "object" ? server.api_key : undefined,
    })),
    ...mcpConfig.stdio_servers.map((server, index) => ({
      id: `stdio-${index}`,
      type: "stdio" as const,
      name: server.name,
      command: server.command,
      args: server.args,
      env: server.env,
    })),
    ...mcpConfig.shttp_servers.map((server, index) => ({
      id: `shttp-${index}`,
      type: "shttp" as const,
      name: typeof server === "object" ? server.name : undefined,
      url: typeof server === "string" ? server : server.url,
      api_key: typeof server === "object" ? server.api_key : undefined,
      timeout: typeof server === "object" ? server.timeout : undefined,
    })),
  ];

  useEffect(() => {
    setSearchApiKey(settings?.search_api_key ?? "");
    setSearchApiKeyDirty(false);
  }, [settings?.search_api_key]);

  const closeServerModal = () => {
    setEditingServer(null);
    setView("list");
  };

  const handleAddServer = (serverConfig: MCPServerConfig) => {
    addMcpServer(serverConfig, {
      onSuccess: closeServerModal,
    });
  };

  const handleEditServer = (serverConfig: MCPServerConfig) => {
    updateMcpServer(
      {
        serverId: serverConfig.id,
        server: serverConfig,
      },
      {
        onSuccess: closeServerModal,
      },
    );
  };

  const handleDeleteServer = (serverId: string) => {
    deleteMcpServer(serverId, {
      onSuccess: () => {
        setConfirmationModalIsVisible(false);
      },
    });
  };

  const handleEditClick = (server: MCPServerConfig) => {
    setEditingServer(server);
    setView("edit");
  };

  const handleDeleteClick = (serverId: string) => {
    setServerToDelete(serverId);
    setConfirmationModalIsVisible(true);
  };

  const handleConfirmDelete = () => {
    if (serverToDelete) {
      handleDeleteServer(serverToDelete);
    }
  };

  const handleCancelDelete = () => {
    setConfirmationModalIsVisible(false);
    setServerToDelete(null);
  };

  const handleSaveSearchApiKey = () => {
    saveSettings(
      { search_api_key: searchApiKey },
      {
        onError: (error) => {
          const message = retrieveAxiosErrorMessage(error as AxiosError);
          displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
        },
        onSuccess: () => {
          displaySuccessToast(t(I18nKey.SETTINGS$SAVED_WARNING));
          setSearchApiKeyDirty(false);
        },
      },
    );
  };

  if (isLoading || !settings) {
    return null;
  }

  return (
    <div data-testid="mcp-settings-screen" className="flex flex-col gap-6">
      <div className="flex items-start justify-between gap-4">
        <header className="min-w-0 space-y-1">
          <Typography.H2>{t(I18nKey.SETTINGS$MCP_TITLE)}</Typography.H2>
          <p
            data-testid="settings-page-subtitle"
            className="text-sm leading-5 text-muted"
          >
            {t(I18nKey.SETTINGS$MCP_DESCRIPTION)}
          </p>
        </header>
        <BrandButton
          type="button"
          variant="primary"
          className="shrink-0 whitespace-nowrap"
          onClick={() => setView("add")}
        >
          {t(I18nKey.SETTINGS$MCP_ADD_SERVER)}
        </BrandButton>
      </div>

      <MCPServerList
        servers={allServers}
        onEdit={handleEditClick}
        onDelete={handleDeleteClick}
      />

      {!isSaasMode ? (
        <section
          data-testid="mcp-search-settings-section"
          className="flex flex-col gap-4 rounded-xl border border-[var(--oh-border)] bg-base-secondary p-5"
        >
          <div className="flex flex-col gap-2">
            <Typography.H3>
              {t(I18nKey.SETTINGS$MCP_SEARCH_TITLE)}
            </Typography.H3>
            <Typography.Paragraph className="text-sm leading-5 text-muted">
              {t(I18nKey.SETTINGS$MCP_SEARCH_DESCRIPTION)}
            </Typography.Paragraph>
          </div>

          <div className="max-w-xl flex flex-col gap-4">
            <SettingsInput
              testId="search-api-key-input"
              label={t(I18nKey.SETTINGS$SEARCH_API_KEY)}
              type="password"
              className="w-full"
              value={searchApiKey}
              placeholder={t(I18nKey.API$TVLY_KEY_EXAMPLE)}
              onChange={(value) => {
                setSearchApiKey(value);
                setSearchApiKeyDirty(value !== (settings.search_api_key ?? ""));
              }}
              startContent={
                settings.search_api_key_set ? (
                  <KeyStatusIcon isSet={settings.search_api_key_set} />
                ) : undefined
              }
            />

            <HelpLink
              testId="search-api-key-help-anchor"
              text={t(I18nKey.SETTINGS$SEARCH_API_KEY_OPTIONAL)}
              linkText={t(I18nKey.SETTINGS$SEARCH_API_KEY_INSTRUCTIONS)}
              href="https://tavily.com/"
            />

            <div>
              <BrandButton
                testId="save-search-api-key-button"
                type="button"
                variant="primary"
                isDisabled={isSavingSearchApiKey || !searchApiKeyDirty}
                onClick={handleSaveSearchApiKey}
              >
                {isSavingSearchApiKey
                  ? t(I18nKey.SETTINGS$SAVING)
                  : t(I18nKey.SETTINGS$SAVE_CHANGES)}
              </BrandButton>
            </div>
          </div>
        </section>
      ) : null}

      {view === "add" && (
        <MCPServerModal
          mode="add"
          existingServers={allServers}
          onSubmit={handleAddServer}
          onClose={closeServerModal}
        />
      )}

      {view === "edit" && editingServer && (
        <MCPServerModal
          mode="edit"
          server={editingServer}
          existingServers={allServers}
          onSubmit={handleEditServer}
          onClose={closeServerModal}
        />
      )}

      {confirmationModalIsVisible && serverToDelete && (
        <ConfirmationModal
          text={t(I18nKey.SETTINGS$MCP_CONFIRM_DELETE)}
          onCancel={handleCancelDelete}
          onConfirm={handleConfirmDelete}
        />
      )}
    </div>
  );
}

export default MCPSettingsScreen;
