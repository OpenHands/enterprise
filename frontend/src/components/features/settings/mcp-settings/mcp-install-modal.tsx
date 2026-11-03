import React from "react";
import { useTranslation } from "react-i18next";
import { AxiosError } from "axios";
import McpService from "#/api/mcp-service/mcp-service.api";
import { TabButton } from "#/components/features/conversation-panel/system-message-modal/tab-button";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { BrandBadge } from "#/components/shared/badge";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import {
  toSdkMcpServer,
  useInstallMcpServer,
  type McpLibraryServer,
} from "#/hooks/mutation/use-install-mcp-server";
import { useSaveFieldsAsSecrets } from "#/hooks/mutation/use-save-fields-as-secrets";
import { useConfig } from "#/hooks/query/use-config";
import { useUserProviders } from "#/hooks/use-user-providers";
import { I18nKey } from "#/i18n/declaration";
import type { MCPAuthCredential } from "#/types/settings";
import { displaySuccessToast } from "#/utils/custom-toast-handlers";
import {
  getInstallableMcpConnectionOptions,
  getMcpOAuthAuthentication,
  getNativeGitProvider,
  makeMcpTestErrorMessage,
  toMcpServerName,
  type McpLibraryEntry,
  type McpLibraryField,
  type McpLibraryOption,
} from "#/utils/mcp-library";
import { authorizeMcpOAuth } from "#/utils/mcp-oauth-flow";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { McpLogoBadge } from "./mcp-logo-badge";
import { McpNativeIntegrationPanel } from "./mcp-native-integration-panel";
import { McpSaveAsSecretToggle } from "./mcp-save-as-secret-toggle";

/**
 * Renders `[text](url)` markdown links in a catalog helper text as anchors.
 * Only http(s) URLs become links; anything else falls back to `#`.
 */
function renderHelperText(text: string): React.ReactNode {
  const linkPattern = /\[([^\]]+)\]\(([^)]+)\)/g;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(linkPattern)) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <a
        key={match.index}
        href={/^https?:\/\//i.test(match[2]) ? match[2] : "#"}
        target="_blank"
        rel="noreferrer"
        className="underline transition-colors hover:text-white"
      >
        {match[1]}
      </a>,
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

interface FieldState {
  values: Record<string, string>;
  errors: Record<string, string | null>;
  savedAsSecret: Record<string, boolean>;
}

function isRemoteOption(option: McpLibraryOption | undefined): boolean {
  return option?.transport.kind === "shttp" || option?.transport.kind === "sse";
}

function needsCredentialField(option: McpLibraryOption | undefined): boolean {
  return (
    isRemoteOption(option) &&
    ["api_key", "bearer"].includes(option!.auth.strategy)
  );
}

function isCredentialOptional(option: McpLibraryOption): boolean {
  if (option.transport.kind === "stdio") {
    return option.auth.apiKeyOptional ?? false;
  }
  return option.auth.apiKeyOptional ?? option.transport.apiKeyOptional ?? false;
}

function getHeaderFields(
  option: McpLibraryOption | undefined,
): McpLibraryField[] {
  if (option?.transport.kind !== "shttp" && option?.transport.kind !== "sse") {
    return [];
  }
  return option.transport.headerFields ?? [];
}

function getOptionLabelKey(option: McpLibraryOption): I18nKey {
  if (option.auth.strategy === "oauth2") return I18nKey.MCP$OPTION_OAUTH;
  if (["api_key", "bearer"].includes(option.auth.strategy)) {
    return I18nKey.MCP$OPTION_API_KEY;
  }
  return option.transport.kind === "stdio"
    ? I18nKey.MCP$OPTION_LOCAL
    : I18nKey.MCP$OPTION_REMOTE;
}

function makeInitialState(option: McpLibraryOption | undefined): FieldState {
  const values: Record<string, string> = {};
  const savedAsSecret: Record<string, boolean> = {};
  const transport = option?.transport;
  if (transport?.kind === "stdio") {
    for (const field of transport.envFields ?? []) {
      values[field.key] = "";
      savedAsSecret[field.key] = field.type === "password";
    }
    for (const field of transport.argFields ?? []) {
      values[field.key] = "";
    }
  } else if (transport) {
    values.url = transport.url;
    for (const field of getHeaderFields(option)) {
      values[field.key] = "";
      savedAsSecret[field.key] = field.type === "password";
    }
    if (needsCredentialField(option)) {
      values.api_key = "";
      if (option?.auth.credentialSecretName) {
        savedAsSecret.api_key =
          option.auth.saveCredentialAsSecretByDefault ?? false;
      }
    }
  }
  return { values, errors: {}, savedAsSecret };
}

interface McpInstallModalProps {
  entry: McpLibraryEntry;
  /** Names the server must not take: the organization's listings. */
  reservedNames: string[];
  onClose: () => void;
}

/**
 * Installs an MCP Library entry as one of the member's own servers
 * (OHE-3870), like the agent-canvas MCP page does. Every install adds a new
 * server; a taken name gets a `_1`, `_2`, … suffix.
 */
export function McpInstallModal({
  entry,
  reservedNames,
  onClose,
}: McpInstallModalProps) {
  const { t } = useTranslation();
  const { mutateAsync: installServer } = useInstallMcpServer();
  const saveFieldsAsSecrets = useSaveFieldsAsSecrets();
  const { data: config } = useConfig();
  const { providers: connectedProviders } = useUserProviders();

  const options = React.useMemo(
    () => getInstallableMcpConnectionOptions(entry),
    [entry],
  );
  const [selectedOptionIndex, setSelectedOptionIndex] = React.useState(0);
  const option = options[selectedOptionIndex];
  const transport = option?.transport;

  const [state, setState] = React.useState<FieldState>(() =>
    makeInitialState(option),
  );
  const [globalError, setGlobalError] = React.useState<string | null>(null);
  const [phase, setPhase] = React.useState<"idle" | "verifying" | "saving">(
    "idle",
  );
  const isPending = phase !== "idle";

  // GitHub, GitLab and Bitbucket can also connect as the instance's native
  // git integration when it has that provider configured; that is offered
  // first, next to the MCP server.
  const nativeProvider = getNativeGitProvider(entry.id);
  const native =
    nativeProvider && config?.providers_configured?.includes(nativeProvider)
      ? {
          provider: nativeProvider,
          isConnected: connectedProviders.includes(nativeProvider),
        }
      : null;
  const [selectedConnection, setSelectedConnection] = React.useState<
    "native" | "mcp"
  >("native");
  const showConnectionTabs = !!native && !!option;
  const isNativeSelected =
    !!native && (!option || selectedConnection === "native");

  const setValue = (key: string, value: string) => {
    setState((prev) => ({
      ...prev,
      values: { ...prev.values, [key]: value },
      errors: { ...prev.errors, [key]: null },
    }));
    setGlobalError(null);
  };

  const selectOption = (index: number) => {
    setSelectedOptionIndex(index);
    setState(makeInitialState(options[index]));
    setGlobalError(null);
  };

  const toggleSecret = (key: string, value: boolean) => {
    setState((prev) => ({
      ...prev,
      savedAsSecret: { ...prev.savedAsSecret, [key]: value },
    }));
  };

  const saveSelectedSecrets = (): Promise<unknown> => {
    if (transport?.kind === "stdio") {
      return saveFieldsAsSecrets(
        transport.envFields ?? [],
        state.values,
        state.savedAsSecret,
      );
    }
    const pending = [
      saveFieldsAsSecrets(
        getHeaderFields(option),
        state.values,
        state.savedAsSecret,
      ),
    ];
    // The hosted credential is saved under the entry's own secret name.
    const secretName = option?.auth.credentialSecretName;
    const apiKey = state.values.api_key?.trim();
    if (secretName && apiKey && state.savedAsSecret.api_key) {
      pending.push(
        saveFieldsAsSecrets(
          [
            {
              key: secretName,
              label: option.auth.credentialLabel ?? secretName,
              type: "password",
              required: true,
            },
          ],
          { [secretName]: apiKey },
          { [secretName]: true },
        ),
      );
    }
    return Promise.all(pending);
  };

  const showError = (error: unknown) => {
    const message = retrieveAxiosErrorMessage(error as AxiosError);
    setGlobalError(message || t(I18nKey.ERROR$GENERIC));
  };

  /** Verify the server (OAuth sign-in or connection test), then save it. */
  const submitServer = async (server: McpLibraryServer) => {
    setPhase("verifying");
    let serverToSave = server;
    try {
      if (server.type !== "stdio" && server.auth?.strategy === "oauth2") {
        const result = await authorizeMcpOAuth({
          name: server.name,
          server: toSdkMcpServer(server),
        });
        if (!result.ok) {
          setGlobalError(
            makeMcpTestErrorMessage(
              t,
              result.errorKind,
              result.error,
              server.type,
            ),
          );
          setPhase("idle");
          return;
        }
        if (result.oauthState) {
          serverToSave = {
            ...server,
            auth: { ...server.auth, state: result.oauthState },
          };
        }
      } else if (server.type !== "stdio") {
        // stdio servers only run inside the sandbox, so the app server cannot
        // test them before they are saved.
        const result = await McpService.testServer({
          name: server.name,
          server: toSdkMcpServer(server),
        });
        if (!result.ok) {
          setGlobalError(
            makeMcpTestErrorMessage(
              t,
              result.error_kind,
              result.error,
              server.type,
            ),
          );
          setPhase("idle");
          return;
        }
      }

      setPhase("saving");
      await installServer({ server: serverToSave, reservedNames });
    } catch (error) {
      showError(error);
      setPhase("idle");
      return;
    }

    displaySuccessToast(t(I18nKey.MCP$INSTALL_SUCCESS));
    try {
      await saveSelectedSecrets();
    } finally {
      onClose();
    }
  };

  const handleRemoteSubmit = () => {
    if (!option || (transport?.kind !== "shttp" && transport?.kind !== "sse")) {
      return;
    }
    const apiKey = state.values.api_key?.trim() ?? "";
    const url = transport.urlEditable
      ? (state.values.url?.trim() ?? "")
      : transport.url;
    const oauthMode = option.auth.strategy === "oauth2";
    const needsCredential = needsCredentialField(option);
    const headerFields = getHeaderFields(option);

    const errors: Record<string, string | null> = {};
    if (!url) {
      errors.url = t(I18nKey.SETTINGS$MCP_ERROR_URL_REQUIRED);
    } else {
      try {
        const parsedUrl = new URL(url);
        if (!["http:", "https:"].includes(parsedUrl.protocol)) {
          errors.url = t(I18nKey.SETTINGS$MCP_ERROR_URL_INVALID_PROTOCOL);
        }
      } catch {
        errors.url = t(I18nKey.SETTINGS$MCP_ERROR_URL_INVALID);
      }
    }
    for (const field of headerFields) {
      if (field.required && !(state.values[field.key] ?? "").trim()) {
        errors[field.key] = t(I18nKey.MCP$ERROR_FIELD_REQUIRED);
      }
    }
    if (
      !oauthMode &&
      needsCredential &&
      !isCredentialOptional(option) &&
      !apiKey
    ) {
      errors.api_key = t(I18nKey.MCP$ERROR_FIELD_REQUIRED);
    }
    if (Object.values(errors).some(Boolean)) {
      setState((prev) => ({ ...prev, errors }));
      return;
    }

    const fieldHeaders = Object.fromEntries(
      headerFields
        .map((field) => [field.key, state.values[field.key]?.trim() ?? ""])
        .filter(([, value]) => value),
    );
    const hasFieldHeaders = Object.keys(fieldHeaders).length > 0;
    let auth: MCPAuthCredential | undefined;
    if (oauthMode) {
      const authentication = getMcpOAuthAuthentication(option);
      auth = { strategy: "oauth2", ...(authentication && { authentication }) };
    } else if (needsCredential && apiKey) {
      auth =
        option.auth.strategy === "api_key"
          ? {
              strategy: "api_key",
              value: apiKey,
              ...(option.auth.apiKeyHeaderName && {
                header_name: option.auth.apiKeyHeaderName,
              }),
            }
          : { strategy: "bearer", value: apiKey };
    } else if (hasFieldHeaders) {
      auth = { strategy: "header", headers: fieldHeaders };
    }

    submitServer({
      type: transport.kind,
      // Named after the catalog entry (e.g. "github"), not the generic
      // "shttp"/"sse" fallback, so profiles can reference it.
      name: toMcpServerName(entry.id),
      url,
      ...(auth && { auth }),
      ...(hasFieldHeaders &&
        auth?.strategy !== "header" && { headers: fieldHeaders }),
    });
  };

  const handleStdioSubmit = () => {
    if (transport?.kind !== "stdio") return;
    const errors: Record<string, string | null> = {};
    for (const field of [
      ...(transport.envFields ?? []),
      ...(transport.argFields ?? []),
    ]) {
      if (field.required && !(state.values[field.key] ?? "").trim()) {
        errors[field.key] = t(I18nKey.MCP$ERROR_FIELD_REQUIRED);
      }
    }
    if (Object.values(errors).some(Boolean)) {
      setState((prev) => ({ ...prev, errors }));
      return;
    }

    const env: Record<string, string> = {};
    for (const field of transport.envFields ?? []) {
      const value = state.values[field.key]?.trim();
      if (value) env[field.key] = value;
    }
    // Argument fields may hold several tokens (e.g. allowed directories).
    const extraArgs = (transport.argFields ?? []).flatMap((field) =>
      (state.values[field.key]?.trim() ?? "").split(/\s+/).filter(Boolean),
    );

    submitServer({
      type: "stdio",
      name: transport.serverName,
      command: transport.command,
      args: [...transport.args, ...extraArgs],
      ...(Object.keys(env).length > 0 && { env }),
    });
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setGlobalError(null);
    if (transport?.kind === "stdio") {
      handleStdioSubmit();
    } else if (transport) {
      handleRemoteSubmit();
    } else {
      setGlobalError(t(I18nKey.MCP$INSTALL_UNAVAILABLE, { name: entry.name }));
    }
  };

  const renderField = (
    field: McpLibraryField,
    { withSecretToggle }: { withSecretToggle: boolean },
  ) => (
    <div key={field.key} className="flex flex-col gap-1">
      <SettingsInput
        testId={`mcp-install-field-${field.key}`}
        name={field.key}
        type={field.type === "password" ? "password" : "text"}
        label={field.label}
        value={state.values[field.key] ?? ""}
        onChange={(value) => setValue(field.key, value)}
        placeholder={field.placeholder}
        required={field.required}
        showOptionalTag={!field.required}
        className="w-full"
      />
      {field.helperText && (
        <p className="text-xs text-tertiary-alt">
          {renderHelperText(field.helperText)}
        </p>
      )}
      {state.errors[field.key] && (
        <p className="text-xs text-red-500">{state.errors[field.key]}</p>
      )}
      {withSecretToggle && field.key in state.savedAsSecret && (
        <McpSaveAsSecretToggle
          fieldKey={field.key}
          checked={state.savedAsSecret[field.key] ?? false}
          onToggle={(value) => toggleSecret(field.key, value)}
        />
      )}
    </div>
  );

  const renderFields = () => {
    if (transport?.kind === "stdio") {
      return (
        <>
          <SettingsInput
            testId="mcp-install-field-command-readonly"
            name="command-readonly"
            type="text"
            label={t(I18nKey.MCP$COMMAND_LABEL)}
            value={`${transport.command} ${transport.args.join(" ")}`.trim()}
            isDisabled
            className="w-full"
          />
          {(transport.envFields ?? []).map((field) =>
            renderField(field, { withSecretToggle: true }),
          )}
          {/* Argument fields are CLI arguments, not credentials. */}
          {(transport.argFields ?? []).map((field) =>
            renderField(field, { withSecretToggle: false }),
          )}
        </>
      );
    }
    if (!transport || !option) return null;

    const { credentialSecretName } = option.auth;
    const apiKeyOptional = isCredentialOptional(option);
    let authFields: React.ReactNode = null;
    if (option.auth.strategy === "oauth2") {
      authFields = (
        <div
          data-testid="mcp-install-oauth-info"
          className="flex flex-col gap-2 rounded-lg border border-[var(--oh-border)] p-3"
        >
          <p className="text-sm text-white">
            {t(I18nKey.MCP$OAUTH_CONNECT_INFO)}
          </p>
          <p className="text-xs text-tertiary-alt">
            {t(I18nKey.MCP$OAUTH_CONNECT_HINT)}
          </p>
        </div>
      );
    } else if (needsCredentialField(option)) {
      authFields = (
        <div className="flex flex-col gap-1">
          <SettingsInput
            testId="mcp-install-field-api_key"
            name="api_key"
            type="password"
            label={
              option.auth.credentialLabel ?? t(I18nKey.SETTINGS$MCP_API_KEY)
            }
            value={state.values.api_key ?? ""}
            onChange={(value) => setValue("api_key", value)}
            placeholder={
              option.auth.credentialPlaceholder ??
              t(I18nKey.SETTINGS$MCP_API_KEY_PLACEHOLDER)
            }
            showOptionalTag={apiKeyOptional}
            required={!apiKeyOptional}
            className="w-full"
          />
          {option.auth.credentialHelp && (
            <p className="text-xs text-tertiary-alt">
              {renderHelperText(option.auth.credentialHelp)}
            </p>
          )}
          {state.errors.api_key && (
            <p className="text-xs text-red-500">{state.errors.api_key}</p>
          )}
          {credentialSecretName && (
            <McpSaveAsSecretToggle
              fieldKey={credentialSecretName}
              checked={state.savedAsSecret.api_key ?? false}
              onToggle={(value) => toggleSecret("api_key", value)}
            />
          )}
        </div>
      );
    }

    return (
      <>
        <SettingsInput
          testId="mcp-install-field-url"
          name="url"
          type="url"
          label={t(I18nKey.SETTINGS$MCP_URL)}
          value={state.values.url ?? transport.url}
          onChange={(value) => setValue("url", value)}
          isDisabled={!transport.urlEditable}
          className="w-full"
        />
        {state.errors.url && (
          <p className="text-xs text-red-500">{state.errors.url}</p>
        )}
        {getHeaderFields(option).map((field) =>
          renderField(field, { withSecretToggle: true }),
        )}
        {authFields}
      </>
    );
  };

  let submitLabel = t(I18nKey.MCP$INSTALL_BUTTON);
  if (phase === "verifying") submitLabel = t(I18nKey.MCP$VERIFYING);
  if (phase === "saving") submitLabel = t(I18nKey.SETTINGS$SAVING);

  return (
    <ModalBackdrop
      onClose={isPending ? undefined : onClose}
      aria-label={entry.name}
    >
      <form
        data-testid="mcp-install-modal"
        data-library-id={entry.id}
        onSubmit={handleSubmit}
        className="custom-scrollbar flex max-h-[90vh] w-[560px] max-w-[90vw] flex-col gap-4 overflow-y-auto rounded-xl border border-[var(--oh-border)] bg-base-secondary p-6"
      >
        <div className="flex items-start gap-3">
          <McpLogoBadge entry={entry} />
          <div className="flex flex-1 flex-col">
            <h2 className="text-lg font-semibold text-white">{entry.name}</h2>
            <p className="text-xs text-muted">{entry.description}</p>
          </div>
        </div>

        {showConnectionTabs && (
          <div
            role="tablist"
            className="flex border-b border-[var(--oh-border)]"
          >
            <TabButton
              isActive={selectedConnection === "native"}
              onClick={() => setSelectedConnection("native")}
              disabled={isPending}
              className="flex items-center gap-2"
            >
              <span data-testid="mcp-install-tab-native">
                {t(I18nKey.MCP$NATIVE_TAB, { name: entry.name })}
              </span>
              <BrandBadge className="shrink-0 whitespace-nowrap px-2.5 py-1 text-xs">
                {t(I18nKey.SETTINGS$SKILLS_RECOMMENDED)}
              </BrandBadge>
            </TabButton>
            <TabButton
              isActive={selectedConnection === "mcp"}
              onClick={() => setSelectedConnection("mcp")}
            >
              <span data-testid="mcp-install-tab-mcp">
                {t(I18nKey.MCP$MCP_TAB, { name: entry.name })}
              </span>
            </TabButton>
          </div>
        )}

        {isNativeSelected && native ? (
          <McpNativeIntegrationPanel
            entry={entry}
            provider={native.provider}
            isConnected={native.isConnected}
            onConnected={onClose}
          />
        ) : (
          <>
            {showConnectionTabs && (
              <div className="flex flex-col gap-1">
                <h3 className="text-sm font-semibold text-white">
                  {t(I18nKey.MCP$MCP_TAB, { name: entry.name })}
                </h3>
                <p className="text-xs text-muted">
                  {t(I18nKey.MCP$MCP_DESCRIPTION, { name: entry.name })}
                </p>
              </div>
            )}

            {entry.installHint && (
              <p className="text-xs text-muted">{entry.installHint}</p>
            )}

            {entry.docsUrl && (
              <a
                href={entry.docsUrl}
                target="_blank"
                rel="noreferrer"
                className="self-start text-xs text-muted transition-colors hover:text-white hover:underline"
              >
                {t(I18nKey.MCP$VIEW_DOCS)}
              </a>
            )}

            {options.length > 1 && (
              <div
                role="tablist"
                aria-label={t(I18nKey.MCP$CONNECTION_METHOD)}
                data-testid="mcp-install-options"
                className="flex flex-wrap border-b border-[var(--oh-border)]"
              >
                {options.map((candidate, index) => (
                  <TabButton
                    key={candidate.id}
                    isActive={index === selectedOptionIndex}
                    onClick={() => selectOption(index)}
                    disabled={isPending}
                  >
                    <span data-testid={`mcp-install-option-${candidate.id}`}>
                      {t(getOptionLabelKey(candidate))}
                    </span>
                  </TabButton>
                ))}
              </div>
            )}

            <div className="flex flex-col gap-3">{renderFields()}</div>

            {!option && (
              <div
                data-testid="mcp-install-unavailable"
                className="flex flex-col gap-2 rounded-lg border border-[var(--oh-border)] p-3"
              >
                <p className="text-sm text-white">
                  {t(I18nKey.MCP$INSTALL_UNAVAILABLE, { name: entry.name })}
                </p>
              </div>
            )}

            {globalError && (
              <p
                data-testid="mcp-install-modal-error"
                className="whitespace-pre-wrap text-sm text-red-500"
              >
                {globalError}
              </p>
            )}

            <div className="mt-2 flex justify-end gap-2">
              <BrandButton
                type="button"
                variant="secondary"
                onClick={onClose}
                testId="mcp-install-cancel"
                isDisabled={isPending}
              >
                {t(I18nKey.BUTTON$CANCEL)}
              </BrandButton>
              <BrandButton
                type="submit"
                variant="primary"
                isDisabled={isPending || !option}
                testId="mcp-install-submit"
              >
                {submitLabel}
              </BrandButton>
            </div>
          </>
        )}
      </form>
    </ModalBackdrop>
  );
}
