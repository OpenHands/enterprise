import React, { useEffect, useMemo, useState } from "react";
import { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useSettings } from "#/hooks/query/use-settings";
import { useConfig } from "#/hooks/query/use-config";
import { useSaveSettings } from "#/hooks/mutation/use-save-settings";
import { useDeleteMcpServer } from "#/hooks/mutation/use-delete-mcp-server";
import { useAddMcpServer } from "#/hooks/mutation/use-add-mcp-server";
import { useUpdateMcpServer } from "#/hooks/mutation/use-update-mcp-server";
import { useTestMcpServer } from "#/hooks/mutation/use-test-mcp-server";
import { I18nKey } from "#/i18n/declaration";

import { MCPServerList } from "#/components/features/settings/mcp-settings/mcp-server-list";
import { MCPServerModal } from "#/components/features/settings/mcp-settings/mcp-server-modal";
import { OrgMcpServerList } from "#/components/features/settings/mcp-settings/org-mcp-server-list";
import { McpLibrarySection } from "#/components/features/settings/mcp-settings/mcp-library-section";
import { McpInstallModal } from "#/components/features/settings/mcp-settings/mcp-install-modal";
import type { McpLibraryEntry } from "#/utils/mcp-library";
import {
  OrgMcpConnectModal,
  OrgMcpServerModal,
} from "#/components/features/settings/mcp-settings/org-mcp-server-modals";
import { notifySuperAdminSetupStep } from "#/components/features/super-admin/super-admin-setup";
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
import { cn } from "#/utils/utils";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { getActiveOrganizationUser } from "#/utils/org/permission-checks";
import { Typography } from "#/ui/typography";
import { MCPTestResponse } from "#/api/mcp-service/mcp-service.types";
import { OrgMcpServerCreate } from "#/api/org-mcp-servers.types";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { useSearchSecrets } from "#/hooks/query/use-get-secrets";
import { useOrgMcpServers } from "#/hooks/query/use-org-mcp-servers";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import {
  useConnectOrgMcpServer,
  useCreateOrgMcpServer,
  useDeleteOrgMcpServer,
  useDisconnectOrgMcpServer,
  useTestOrgMcpServer,
  useUpdateOrgMcpServer,
} from "#/hooks/mutation/use-org-mcp-server-mutations";
import {
  McpOAuthFlowFailure,
  McpOAuthFlowResult,
  runMcpOAuthFlow,
} from "#/utils/mcp-oauth-flow";
import {
  OrgMcpServerPrefill,
  OrgMcpServerView,
  toOrgMcpServerView,
} from "#/utils/org-mcp-servers";

export const clientLoader = createPermissionGuard(
  "manage_mcp",
  undefined,
  async () => {
    const user = await getActiveOrganizationUser();
    return user?.permissions?.includes("manage_org_mcp") ?? false;
  },
);
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

/** The inline message a connection test leaves in the form, if any. */
function describeTestResult(
  t: TFunction,
  testResult: MCPTestResponse | undefined,
  testError: unknown,
): { ok: boolean; text: string } | null {
  if (testError) {
    return {
      ok: false,
      text:
        retrieveAxiosErrorMessage(testError as AxiosError) ||
        t(I18nKey.ERROR$GENERIC),
    };
  }
  if (!testResult) return null;
  if (testResult.ok) {
    return {
      ok: true,
      text: t(I18nKey.SETTINGS$MCP_TEST_SUCCESS, {
        count: testResult.tools.length,
      }),
    };
  }
  switch (testResult.error_kind) {
    case "timeout":
      return { ok: false, text: t(I18nKey.SETTINGS$MCP_TEST_ERROR_TIMEOUT) };
    case "connection":
      return {
        ok: false,
        text: t(I18nKey.SETTINGS$MCP_TEST_ERROR_CONNECTION),
      };
    default:
      return {
        ok: false,
        text: t(I18nKey.SETTINGS$MCP_TEST_ERROR_UNKNOWN, {
          error: testResult.error,
        }),
      };
  }
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
  const {
    mutate: testMcpServer,
    isPending: isTestingServer,
    data: testResult,
    error: testError,
    reset: resetTest,
  } = useTestMcpServer();

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

  // Organization MCP server listings (OHE-3091), behind a flag until bug
  // bash is complete. Admins manage them; every member launches with them
  // and connects the user-managed ones with a personal secret. A personal
  // workspace has no organization to share them with (OHE-3864).
  const { isTeamOrg } = useOrgTypeAndAccess();
  const orgMcpEnabled =
    !!config?.feature_flags?.enable_org_mcp_servers && isTeamOrg;
  const { hasPermission } = usePermission();
  const canManageOrgMcp = orgMcpEnabled && hasPermission("manage_org_mcp");
  const orgMcpServersQuery = useOrgMcpServers();
  const orgListings = useMemo(
    () => orgMcpServersQuery.data.map(toOrgMcpServerView),
    [orgMcpServersQuery.data],
  );
  const hasUserManagedListings = orgListings.some(
    (listing) => listing.managedBy === "user",
  );
  // "Connected" means the member already holds the secrets a user-managed
  // listing references, so page through every secret the member can use.
  const secretsQuery = useSearchSecrets({
    pageSize: 100,
    enabled: orgMcpEnabled && hasUserManagedListings,
  });
  const {
    hasNextPage: hasMoreSecrets,
    isFetchingNextPage: isFetchingMoreSecrets,
    isError: isSecretsError,
    fetchNextPage: fetchMoreSecrets,
  } = secretsQuery;
  useEffect(() => {
    if (hasMoreSecrets && !isFetchingMoreSecrets && !isSecretsError) {
      fetchMoreSecrets();
    }
  }, [hasMoreSecrets, isFetchingMoreSecrets, isSecretsError, fetchMoreSecrets]);
  const availableSecretNames = useMemo(
    () => new Set(secretsQuery.data.map((secret) => secret.name)),
    [secretsQuery.data],
  );
  const { mutate: createOrgMcpServer, isPending: isCreatingOrgServer } =
    useCreateOrgMcpServer();
  const { mutate: updateOrgMcpServer, isPending: isUpdatingOrgServer } =
    useUpdateOrgMcpServer();
  const { mutate: deleteOrgMcpServer } = useDeleteOrgMcpServer();
  const { mutateAsync: connectOrgMcpServer } = useConnectOrgMcpServer();
  const { mutate: disconnectOrgMcpServer } = useDisconnectOrgMcpServer();
  const [connectingOAuthName, setConnectingOAuthName] = useState<string | null>(
    null,
  );
  const {
    mutate: testOrgMcpServer,
    isPending: isTestingOrgServer,
    data: orgTestResult,
    error: orgTestError,
    reset: resetOrgTest,
  } = useTestOrgMcpServer();
  const [orgView, setOrgView] = useState<"list" | "add" | "edit">("list");
  const [editingListing, setEditingListing] = useState<OrgMcpServerView | null>(
    null,
  );
  const [connectingListing, setConnectingListing] =
    useState<OrgMcpServerView | null>(null);
  const [listingToDelete, setListingToDelete] =
    useState<OrgMcpServerView | null>(null);

  // MCP Library (OHE-3870): the agent-canvas MCP page points here whenever
  // organization MCP servers are on, in any workspace.
  const mcpLibraryEnabled = !!config?.feature_flags?.enable_org_mcp_servers;
  const [libraryEntry, setLibraryEntry] = useState<McpLibraryEntry | null>(
    null,
  );
  // An entry an admin adds to the organization instead (OHE-3883): the
  // organization form opens prefilled from the catalog.
  const [orgPrefill, setOrgPrefill] = useState<OrgMcpServerPrefill | null>(
    null,
  );

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

  const testMessage = useMemo(
    () => describeTestResult(t, testResult, testError),
    [testError, testResult, t],
  );
  const orgTestMessage = useMemo(
    () => describeTestResult(t, orgTestResult, orgTestError),
    [orgTestError, orgTestResult, t],
  );

  const closeServerModal = () => {
    resetTest();
    setEditingServer(null);
    setView("list");
  };

  const handleTestServer = (serverConfig: MCPServerConfig) => {
    testMcpServer({
      serverId: view === "edit" ? editingServer?.id : undefined,
      server: serverConfig,
    });
  };

  const handleAddServer = (serverConfig: MCPServerConfig) => {
    addMcpServer(serverConfig, {
      onSuccess: () => {
        notifySuperAdminSetupStep("add-integration");
        closeServerModal();
      },
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
    resetTest();
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

  const showOrgError = (error: unknown) => {
    const message = retrieveAxiosErrorMessage(error as AxiosError);
    displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
  };

  const closeOrgServerModal = () => {
    resetOrgTest();
    setEditingListing(null);
    setOrgPrefill(null);
    setOrgView("list");
  };

  const handleTestOrgServer = (payload: OrgMcpServerCreate) => {
    testOrgMcpServer({ name: payload.name, server: payload.server });
  };

  const handleAddOrgServer = (payload: OrgMcpServerCreate) => {
    createOrgMcpServer(payload, {
      onSuccess: closeOrgServerModal,
      onError: showOrgError,
    });
  };

  const handleAddLibraryEntryToOrg = (prefill: OrgMcpServerPrefill) => {
    setLibraryEntry(null);
    resetOrgTest();
    setOrgPrefill(prefill);
    setOrgView("add");
  };

  const handleEditOrgServer = (payload: OrgMcpServerCreate) => {
    if (!editingListing) return;
    updateOrgMcpServer(
      {
        name: editingListing.name,
        payload: {
          managed_by: payload.managed_by,
          server: payload.server,
          ...(payload.oauth_job_id && { oauth_job_id: payload.oauth_job_id }),
        },
      },
      { onSuccess: closeOrgServerModal, onError: showOrgError },
    );
  };

  const showOAuthFlowError = (result: McpOAuthFlowFailure) => {
    if (result.reason === "popup_blocked") {
      displayErrorToast(t(I18nKey.SETTINGS$MCP_ORG_OAUTH_POPUP_BLOCKED));
    } else if (result.reason === "not_completed") {
      displayErrorToast(t(I18nKey.SETTINGS$MCP_ORG_OAUTH_NOT_COMPLETED));
    } else {
      displayErrorToast(result.error || t(I18nKey.ERROR$GENERIC));
    }
  };

  const handleAuthorizeOrgServer = async (
    payload: OrgMcpServerCreate,
  ): Promise<McpOAuthFlowResult> => {
    try {
      const result = await runMcpOAuthFlow({
        name: payload.name,
        server: payload.server,
      });
      if (!result.ok) showOAuthFlowError(result);
      return result;
    } catch (error) {
      showOrgError(error);
      return { ok: false, reason: "failed" };
    }
  };

  // A user-managed listing connects with the member's own account (OAuth, right
  // here, so the window opens inside the click) or their own personal secret.
  const handleConnectListing = async (listing: OrgMcpServerView) => {
    if (listing.auth !== "oauth") {
      setConnectingListing(listing);
      return;
    }
    setConnectingOAuthName(listing.name);
    try {
      const result = await handleAuthorizeOrgServer({
        name: listing.name,
        managed_by: listing.managedBy,
        server: listing.server,
      });
      if (result.ok) {
        await connectOrgMcpServer({ name: listing.name, jobId: result.jobId });
      }
    } catch (error) {
      showOrgError(error);
    } finally {
      setConnectingOAuthName(null);
    }
  };

  const handleDisconnectListing = (listing: OrgMcpServerView) => {
    disconnectOrgMcpServer(listing.name, { onError: showOrgError });
  };

  const handleEditListingClick = (listing: OrgMcpServerView) => {
    resetOrgTest();
    setEditingListing(listing);
    setOrgView("edit");
  };

  const handleConfirmDeleteListing = () => {
    if (!listingToDelete) return;
    deleteOrgMcpServer(listingToDelete.name, {
      onSettled: () => setListingToDelete(null),
      onError: showOrgError,
    });
  };

  if (isLoading || !settings) {
    return null;
  }

  const orgResourceOnly = orgMcpEnabled && canManageOrgMcp;
  const showOrgSection =
    orgMcpEnabled && (canManageOrgMcp || orgListings.length > 0);

  return (
    <div data-testid="mcp-settings-screen" className="flex flex-col gap-6">
      {!orgResourceOnly ? (
        <>
          <div className="flex items-start justify-between gap-4">
            {/* Next to the organization's servers, this list is the member's own (OHE-3868). */}
            <header className="min-w-0 space-y-1">
              <Typography.H2>
                {t(
                  orgMcpEnabled
                    ? I18nKey.SETTINGS$MCP_PERSONAL_SECTION_TITLE
                    : I18nKey.SETTINGS$MCP_TITLE,
                )}
              </Typography.H2>
              <p
                data-testid="settings-page-subtitle"
                className="text-sm leading-5 text-muted"
              >
                {t(
                  orgMcpEnabled
                    ? I18nKey.SETTINGS$MCP_PERSONAL_SECTION_DESCRIPTION
                    : I18nKey.SETTINGS$MCP_DESCRIPTION,
                )}
              </p>
            </header>
            <BrandButton
              type="button"
              variant="primary"
              className="shrink-0 whitespace-nowrap"
              onClick={() => {
                resetTest();
                setView("add");
              }}
            >
              {t(I18nKey.SETTINGS$MCP_ADD_SERVER)}
            </BrandButton>
          </div>

          <MCPServerList
            servers={allServers}
            onEdit={handleEditClick}
            onDelete={handleDeleteClick}
          />
        </>
      ) : null}

      {showOrgSection ? (
        <section
          data-testid="org-mcp-servers-section"
          className={cn(
            "flex flex-col gap-4",
            orgResourceOnly
              ? ""
              : "mt-2 border-t border-[var(--oh-border)] pt-6",
          )}
        >
          <div className="flex items-start justify-between gap-4">
            <header className="min-w-0 space-y-1">
              <Typography.H2>
                {t(I18nKey.SETTINGS$MCP_ORG_SECTION_TITLE)}
              </Typography.H2>
              <p className="text-sm leading-5 text-muted">
                {t(
                  canManageOrgMcp
                    ? I18nKey.SETTINGS$MCP_ORG_SECTION_DESCRIPTION_ADMIN
                    : I18nKey.SETTINGS$MCP_ORG_SECTION_DESCRIPTION_MEMBER,
                )}
              </p>
            </header>
            {canManageOrgMcp ? (
              <BrandButton
                testId="add-org-mcp-server-button"
                type="button"
                variant="primary"
                className="shrink-0 whitespace-nowrap"
                onClick={() => {
                  resetOrgTest();
                  setOrgView("add");
                }}
              >
                {t(I18nKey.SETTINGS$MCP_ORG_ADD_SERVER)}
              </BrandButton>
            ) : null}
          </div>
          <OrgMcpServerList
            listings={orgListings}
            availableSecretNames={availableSecretNames}
            canManage={canManageOrgMcp}
            connectingName={connectingOAuthName}
            onEdit={handleEditListingClick}
            onDelete={setListingToDelete}
            onConnect={handleConnectListing}
            onDisconnect={handleDisconnectListing}
          />
        </section>
      ) : null}

      {mcpLibraryEnabled && !orgResourceOnly ? (
        <McpLibrarySection onSelect={setLibraryEntry} />
      ) : null}

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

      {libraryEntry ? (
        <McpInstallModal
          entry={libraryEntry}
          reservedNames={orgListings.map((listing) => listing.name)}
          onAddToOrganization={
            canManageOrgMcp ? handleAddLibraryEntryToOrg : undefined
          }
          onClose={() => setLibraryEntry(null)}
        />
      ) : null}

      {view === "add" && (
        <MCPServerModal
          mode="add"
          existingServers={allServers}
          reservedNames={
            orgMcpEnabled
              ? orgListings.map((listing) => listing.name)
              : undefined
          }
          onSubmit={handleAddServer}
          onClose={closeServerModal}
          onTest={handleTestServer}
          isTestPending={isTestingServer}
          testMessage={testMessage}
        />
      )}

      {view === "edit" && editingServer && (
        <MCPServerModal
          mode="edit"
          server={editingServer}
          existingServers={allServers}
          reservedNames={
            orgMcpEnabled
              ? orgListings.map((listing) => listing.name)
              : undefined
          }
          onSubmit={handleEditServer}
          onClose={closeServerModal}
          onTest={handleTestServer}
          isTestPending={isTestingServer}
          testMessage={testMessage}
        />
      )}

      {confirmationModalIsVisible && serverToDelete && (
        <ConfirmationModal
          text={t(I18nKey.SETTINGS$MCP_CONFIRM_DELETE)}
          onCancel={handleCancelDelete}
          onConfirm={handleConfirmDelete}
        />
      )}

      {orgView === "add" && (
        <OrgMcpServerModal
          mode="add"
          initialValues={orgPrefill ?? undefined}
          existingNames={orgListings.map((listing) => listing.name)}
          onSubmit={handleAddOrgServer}
          onClose={closeOrgServerModal}
          onTest={handleTestOrgServer}
          onAuthorize={handleAuthorizeOrgServer}
          isTestPending={isTestingOrgServer}
          isSaving={isCreatingOrgServer}
          testMessage={orgTestMessage}
        />
      )}

      {orgView === "edit" && editingListing && (
        <OrgMcpServerModal
          mode="edit"
          listing={editingListing}
          existingNames={orgListings
            .map((listing) => listing.name)
            .filter((name) => name !== editingListing.name)}
          onSubmit={handleEditOrgServer}
          onClose={closeOrgServerModal}
          onTest={handleTestOrgServer}
          onAuthorize={handleAuthorizeOrgServer}
          isTestPending={isTestingOrgServer}
          isSaving={isUpdatingOrgServer}
          testMessage={orgTestMessage}
        />
      )}

      {connectingListing && (
        <OrgMcpConnectModal
          listing={connectingListing}
          onClose={() => setConnectingListing(null)}
        />
      )}

      {listingToDelete && (
        <ConfirmationModal
          text={t(I18nKey.SETTINGS$MCP_ORG_CONFIRM_DELETE)}
          onCancel={() => setListingToDelete(null)}
          onConfirm={handleConfirmDeleteListing}
        />
      )}
    </div>
  );
}

export default MCPSettingsScreen;
