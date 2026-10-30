import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { SettingsInput } from "../settings-input";
import { SettingsDropdownInput } from "../settings-dropdown-input";
import { BrandButton } from "../brand-button";
import { OptionalTag } from "../optional-tag";
import { cn } from "#/utils/utils";
import { formControlMultilineFieldClassName } from "#/utils/form-control-classes";
import {
  MCP_SERVER_NAME_PATTERN,
  suggestMcpServerName,
} from "#/utils/mcp-config";

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

export interface TestMessage {
  ok: boolean;
  text: string;
}

interface MCPServerFormProps {
  mode: "add" | "edit";
  server?: MCPServerConfig;
  existingServers?: MCPServerConfig[];
  onSubmit: (server: MCPServerConfig) => void;
  onCancel: () => void;
  onTest?: (server: MCPServerConfig) => void;
  isTestPending?: boolean;
  testMessage?: TestMessage | null;
}

export function MCPServerForm({
  mode,
  server,
  existingServers,
  onSubmit,
  onCancel,
  onTest,
  isTestPending = false,
  testMessage = null,
}: MCPServerFormProps) {
  const { t } = useTranslation();
  const [serverType, setServerType] = React.useState<MCPServerType>(
    server?.type || "sse",
  );
  const [error, setError] = React.useState<string | null>(null);
  const formRef = React.useRef<HTMLFormElement>(null);

  // All server types share one name space: the name is the server's config
  // key, and the agent sees it on the server's tools.
  const otherServerNames = React.useMemo(
    () =>
      (existingServers ?? [])
        .filter((s) => mode === "add" || s.id !== server?.id)
        .map((s) => s.name)
        .filter((n): n is string => !!n),
    [existingServers, mode, server?.id],
  );
  const isRemote = (type: MCPServerType) => type === "sse" || type === "shttp";

  const [url, setUrl] = React.useState(server?.url || "");
  const [name, setName] = React.useState(() => {
    if (server?.name) return server.name;
    // Legacy remote entries saved without a name get a suggested one on edit.
    if (mode === "edit" && server?.url && isRemote(server.type)) {
      return suggestMcpServerName(server.url, server.type, otherServerNames);
    }
    return "";
  });
  // Until the user types a name, a remote server's name follows its URL.
  const [nameEdited, setNameEdited] = React.useState(mode === "edit");

  const suggestName = (type: MCPServerType, forUrl: string) =>
    forUrl.trim() ? suggestMcpServerName(forUrl, type, otherServerNames) : "";

  const handleUrlChange = (value: string) => {
    setUrl(value);
    if (!nameEdited) setName(suggestName(serverType, value));
  };

  const handleNameChange = (value: string) => {
    setName(value);
    setNameEdited(value !== "");
  };

  const handleServerTypeChange = (type: MCPServerType) => {
    setServerType(type);
    if (!nameEdited) setName(isRemote(type) ? suggestName(type, url) : "");
  };

  const serverTypeOptions = [
    { key: "sse", label: t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SSE) },
    { key: "stdio", label: t(I18nKey.SETTINGS$MCP_SERVER_TYPE_STDIO) },
    { key: "shttp", label: t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SHTTP) },
  ];

  const validateUrl = (value: string): string | null => {
    if (!value) return t(I18nKey.SETTINGS$MCP_ERROR_URL_REQUIRED);
    try {
      const urlObj = new URL(value);
      if (!["http:", "https:"].includes(urlObj.protocol)) {
        return t(I18nKey.SETTINGS$MCP_ERROR_URL_INVALID_PROTOCOL);
      }
    } catch {
      return t(I18nKey.SETTINGS$MCP_ERROR_URL_INVALID);
    }
    return null;
  };

  const validateName = (value: string): string | null => {
    if (!value) return t(I18nKey.SETTINGS$MCP_ERROR_NAME_REQUIRED);
    if (!MCP_SERVER_NAME_PATTERN.test(value)) {
      return t(I18nKey.SETTINGS$MCP_ERROR_NAME_INVALID);
    }
    return null;
  };

  const validateNameUniqueness = (candidate: string): string | null => {
    const lower = candidate.toLowerCase();
    if (otherServerNames.some((n) => n.toLowerCase() === lower)) {
      return t(I18nKey.SETTINGS$MCP_ERROR_NAME_DUPLICATE);
    }
    return null;
  };

  const validateServerName = (formData: FormData): string | null => {
    const value = formData.get("name")?.toString().trim() || "";
    // A stored name may predate these rules (e.g. saved by another client);
    // keeping it unchanged must not block other edits.
    if (mode === "edit" && server?.name && value === server.name) return null;
    return validateName(value) ?? validateNameUniqueness(value);
  };

  const validateCommand = (command: string): string | null => {
    if (!command) return t(I18nKey.SETTINGS$MCP_ERROR_COMMAND_REQUIRED);
    if (command.includes(" ")) {
      return t(I18nKey.SETTINGS$MCP_ERROR_COMMAND_NO_SPACES);
    }
    return null;
  };

  const validateUrlUniqueness = (value: string): string | null => {
    if (!existingServers) return null;
    const originalUrl = server?.url;
    const changed =
      mode === "add" || (mode === "edit" && originalUrl !== value);
    if (!changed) return null;
    // For URL-based servers (sse/shttp), ensure URL is unique across both types
    const exists = existingServers.some(
      (s) => (s.type === "sse" || s.type === "shttp") && s.url === value,
    );
    if (exists) return t(I18nKey.SETTINGS$MCP_ERROR_URL_DUPLICATE);
    return null;
  };

  const validateEnvFormat = (envString: string): string | null => {
    if (!envString.trim()) return null;
    const lines = envString.split("\n");
    for (let i = 0; i < lines.length; i += 1) {
      const trimmed = lines[i].trim();
      if (trimmed) {
        const eq = trimmed.indexOf("=");
        if (eq === -1) return t(I18nKey.SETTINGS$MCP_ERROR_ENV_INVALID_FORMAT);
        const key = trimmed.substring(0, eq).trim();
        if (!key) return t(I18nKey.SETTINGS$MCP_ERROR_ENV_INVALID_FORMAT);
      }
    }
    return null;
  };

  const validateTimeout = (timeoutStr: string): string | null => {
    if (!timeoutStr.trim()) return null; // Optional field

    const timeout = parseInt(timeoutStr.trim(), 10);
    if (Number.isNaN(timeout)) {
      return t(I18nKey.SETTINGS$MCP_ERROR_TIMEOUT_INVALID_NUMBER);
    }
    if (timeout <= 0) {
      return t(I18nKey.SETTINGS$MCP_ERROR_TIMEOUT_POSITIVE);
    }
    if (timeout > 3600) {
      return t(I18nKey.SETTINGS$MCP_ERROR_TIMEOUT_MAX_EXCEEDED);
    }
    return null;
  };

  const validateStdioServer = (formData: FormData): string | null => {
    const command = formData.get("command")?.toString().trim() || "";
    const envString = formData.get("env")?.toString() || "";

    const nameError = validateServerName(formData);
    if (nameError) return nameError;

    const commandError = validateCommand(command);
    if (commandError) return commandError;

    // Validate environment variable format
    const envError = validateEnvFormat(envString);
    if (envError) return envError;

    return null;
  };

  const validateForm = (formData: FormData): string | null => {
    if (serverType === "sse" || serverType === "shttp") {
      const formUrl = formData.get("url")?.toString().trim() || "";
      const urlError = validateUrl(formUrl);
      if (urlError) return urlError;
      const urlDupError = validateUrlUniqueness(formUrl);
      if (urlDupError) return urlDupError;
      const nameError = validateServerName(formData);
      if (nameError) return nameError;

      // Validate timeout for SHTTP servers only
      if (serverType === "shttp") {
        const timeoutStr = formData.get("timeout")?.toString() || "";
        const timeoutError = validateTimeout(timeoutStr);
        if (timeoutError) return timeoutError;
      }

      return null;
    }

    if (serverType === "stdio") {
      return validateStdioServer(formData);
    }

    return null;
  };

  const parseEnvironmentVariables = (
    envString: string,
  ): Record<string, string> => {
    const env: Record<string, string> = {};
    const input = envString.trim();
    if (!input) return env;

    for (const line of input.split("\n")) {
      const trimmed = line.trim();
      const eq = trimmed.indexOf("=");
      const key = eq >= 0 ? trimmed.substring(0, eq).trim() : "";
      if (trimmed && eq !== -1 && key) {
        env[key] = trimmed.substring(eq + 1).trim();
      }
    }
    return env;
  };

  const formatEnvironmentVariables = (env?: Record<string, string>): string => {
    if (!env) return "";
    return Object.entries(env)
      .map(([key, value]) => `${key}=${value}`)
      .join("\n");
  };

  const buildConfig = (formData: FormData): MCPServerConfig => {
    const baseConfig = {
      id: server?.id || `${serverType}-${Date.now()}`,
      type: serverType,
    };

    if (serverType === "sse" || serverType === "shttp") {
      const serverUrl = formData.get("url")?.toString().trim();
      const serverName = formData.get("name")?.toString().trim();
      const apiKey = formData.get("api_key")?.toString().trim();
      const timeoutStr = formData.get("timeout")?.toString().trim();

      const serverConfig: MCPServerConfig = {
        ...baseConfig,
        name: serverName!,
        url: serverUrl!,
        ...(apiKey && { api_key: apiKey }),
      };

      // Only add timeout for SHTTP servers
      if (serverType === "shttp" && timeoutStr) {
        const timeoutValue = parseInt(timeoutStr, 10);
        if (!Number.isNaN(timeoutValue)) {
          serverConfig.timeout = timeoutValue;
        }
      }

      return serverConfig;
    }

    // stdio
    const stdioName = formData.get("name")?.toString().trim();
    const command = formData.get("command")?.toString().trim();
    const argsString = formData.get("args")?.toString().trim();
    const envString = formData.get("env")?.toString().trim();

    const args = argsString
      ? argsString
          .split("\n")
          .map((arg) => arg.trim())
          .filter(Boolean)
      : [];
    const env = parseEnvironmentVariables(envString || "");

    return {
      ...baseConfig,
      name: stdioName!,
      command: command!,
      ...(args.length > 0 && { args }),
      ...(Object.keys(env).length > 0 && { env }),
    };
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);

    const formData = new FormData(event.currentTarget);
    const validationError = validateForm(formData);

    if (validationError) {
      setError(validationError);
      return;
    }

    onSubmit(buildConfig(formData));
  };

  const handleTestClick = () => {
    if (!onTest || !formRef.current) return;
    setError(null);
    const formData = new FormData(formRef.current);
    const validationError = validateForm(formData);
    if (validationError) {
      setError(validationError);
      return;
    }
    onTest(buildConfig(formData));
  };

  // Only remote servers can be probed from the settings page; stdio servers
  // run inside the conversation sandbox.
  const canTest = !!onTest && (serverType === "sse" || serverType === "shttp");

  const formTestId =
    mode === "add" ? "add-mcp-server-form" : "edit-mcp-server-form";

  return (
    <form
      ref={formRef}
      data-testid={formTestId}
      onSubmit={handleSubmit}
      className="flex w-full flex-col items-stretch gap-6"
    >
      {mode === "add" && (
        <SettingsDropdownInput
          testId="server-type-dropdown"
          name="server-type"
          label={t(I18nKey.SETTINGS$MCP_SERVER_TYPE)}
          items={serverTypeOptions}
          selectedKey={serverType}
          onSelectionChange={(key) =>
            handleServerTypeChange(key as MCPServerType)
          }
          onInputChange={() => {}} // Prevent input changes
          isClearable={false}
          allowsCustomValue={false}
          required
          wrapperClassName="w-full"
        />
      )}

      {error && <p className="text-red-500 text-sm">{error}</p>}

      {(serverType === "sse" || serverType === "shttp") && (
        <SettingsInput
          testId="url-input"
          name="url"
          type="url"
          label={t(I18nKey.SETTINGS$MCP_URL)}
          className="w-full"
          required
          value={url}
          onChange={handleUrlChange}
          placeholder="https://api.example.com"
        />
      )}

      <SettingsInput
        testId="name-input"
        name="name"
        type="text"
        label={t(I18nKey.SETTINGS$MCP_NAME)}
        hint={t(I18nKey.SETTINGS$MCP_NAME_HINT)}
        className="w-full"
        required
        value={name}
        onChange={handleNameChange}
        placeholder="my-mcp-server"
      />

      {(serverType === "sse" || serverType === "shttp") && (
        <>
          <SettingsInput
            testId="api-key-input"
            name="api_key"
            type="password"
            label={t(I18nKey.SETTINGS$MCP_API_KEY)}
            className="w-full"
            showOptionalTag
            defaultValue={server?.api_key || ""}
            placeholder={t(I18nKey.SETTINGS$MCP_API_KEY_PLACEHOLDER)}
          />

          {serverType === "shttp" && (
            <SettingsInput
              testId="timeout-input"
              name="timeout"
              type="number"
              label={t(I18nKey.SETTINGS$MCP_TIMEOUT_LABEL)}
              className="w-full"
              showOptionalTag
              defaultValue={server?.timeout?.toString() || ""}
              placeholder="60"
              min={1}
              max={3600}
            />
          )}
        </>
      )}

      {serverType === "stdio" && (
        <>
          <SettingsInput
            testId="command-input"
            name="command"
            type="text"
            label={t(I18nKey.SETTINGS$MCP_COMMAND)}
            className="w-full"
            required
            defaultValue={server?.command || ""}
            placeholder="npx"
          />

          <label className="flex w-full flex-col gap-2.5">
            <div className="flex items-center gap-2">
              <span className="text-sm">
                {t(I18nKey.SETTINGS$MCP_COMMAND_ARGUMENTS)}
              </span>
              <OptionalTag />
            </div>
            <textarea
              data-testid="args-input"
              name="args"
              rows={3}
              defaultValue={server?.args?.join("\n") || ""}
              placeholder="arg1&#10;arg2&#10;arg3"
              className={cn(
                formControlMultilineFieldClassName,
                "resize-none",
                "disabled:bg-[var(--oh-surface-raised)] disabled:border-[var(--oh-border-subtle)]",
              )}
            />
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SETTINGS$MCP_COMMAND_ARGUMENTS_HELP)}
            </p>
          </label>

          <label className="flex w-full flex-col gap-2.5">
            <div className="flex items-center gap-2">
              <span className="text-sm">
                {t(I18nKey.SETTINGS$MCP_ENVIRONMENT_VARIABLES)}
              </span>
              <OptionalTag />
            </div>
            <textarea
              data-testid="env-input"
              name="env"
              rows={4}
              defaultValue={formatEnvironmentVariables(server?.env)}
              placeholder="KEY1=value1&#10;KEY2=value2"
              className={cn(
                formControlMultilineFieldClassName,
                "resize-none",
                "disabled:bg-[var(--oh-surface-raised)] disabled:border-[var(--oh-border-subtle)]",
              )}
            />
          </label>
        </>
      )}

      {testMessage && (
        <p
          data-testid="mcp-test-message"
          className={cn(
            "text-sm whitespace-pre-wrap",
            testMessage.ok ? "text-success" : "text-red-500",
          )}
        >
          {testMessage.text}
        </p>
      )}

      <div className="flex w-full items-center justify-end gap-2">
        <BrandButton
          testId="cancel-button"
          type="button"
          variant="secondary"
          onClick={onCancel}
        >
          {t(I18nKey.BUTTON$CANCEL)}
        </BrandButton>
        {canTest && (
          <BrandButton
            testId="mcp-test-connection"
            type="button"
            variant="secondary"
            onClick={handleTestClick}
            isDisabled={isTestPending}
          >
            {isTestPending
              ? t(I18nKey.SETTINGS$MCP_TESTING)
              : t(I18nKey.SETTINGS$MCP_TEST_CONNECTION)}
          </BrandButton>
        )}
        <BrandButton
          testId="submit-button"
          type="submit"
          variant="primary"
          isDisabled={isTestPending}
        >
          {mode === "add" && t(I18nKey.SETTINGS$MCP_ADD_SERVER)}
          {mode === "edit" && t(I18nKey.SETTINGS$MCP_SAVE_SERVER)}
        </BrandButton>
      </div>
    </form>
  );
}
