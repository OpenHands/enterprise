import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import {
  OrgMcpManagedBy,
  OrgMcpServerCreate,
} from "#/api/org-mcp-servers.types";
import {
  ORG_MCP_SERVER_NAME_PATTERN,
  OrgMcpAuthKind,
  OrgMcpServerFormValues,
  OrgMcpServerPrefill,
  OrgMcpServerType,
  OrgMcpServerView,
  REDACTED_MCP_SECRET,
  SECRET_NAME_PATTERN,
  buildOrgMcpServerPayload,
} from "#/utils/org-mcp-servers";
import { McpOAuthFlowResult } from "#/utils/mcp-oauth-flow";
import { SettingsInput } from "../settings-input";
import { SettingsDropdownInput } from "../settings-dropdown-input";
import { BrandButton } from "../brand-button";
import { OptionalTag } from "../optional-tag";
import { cn } from "#/utils/utils";
import { formControlMultilineFieldClassName } from "#/utils/form-control-classes";
import {
  formatEnvironmentVariables,
  parseEnvironmentVariables,
} from "./mcp-server-form";
import type { TestMessage } from "./mcp-server-form";

/** The system OpenHands MCP server is injected under this name at launch. */
const RESERVED_NAMES = ["default"];

interface OrgMcpServerFormProps {
  mode: "add" | "edit";
  listing?: OrgMcpServerView;
  /** Add mode: defaults from an MCP Library entry (OHE-3883). */
  initialValues?: OrgMcpServerPrefill;
  /** Names of the organization's other listings (uniqueness when adding). */
  existingNames?: string[];
  onSubmit: (payload: OrgMcpServerCreate) => void;
  onCancel: () => void;
  onTest?: (payload: OrgMcpServerCreate) => void;
  /** Run the OAuth consent flow; must open its window synchronously. */
  onAuthorize?: (payload: OrgMcpServerCreate) => Promise<McpOAuthFlowResult>;
  isTestPending?: boolean;
  isSaving?: boolean;
  testMessage?: TestMessage | null;
}

/**
 * Add / edit one organization MCP server listing (OHE-3091).
 *
 * The credential field depends on who manages the listing: an
 * organization-managed listing stores the admin's token (masked after save,
 * kept when left blank while editing, required again after a URL change);
 * a user-managed listing stores the name of the personal secret each member
 * creates to connect. With OAuth, the admin authorizes an organization-managed
 * listing here before saving it, and each member authorizes a user-managed one
 * from the list. A stdio listing (OHE-3881) takes a command, arguments and
 * environment variables instead of a URL and credential; a user-managed one
 * references each member's personal secrets as `${NAME}` in its env.
 */
export function OrgMcpServerForm({
  mode,
  listing,
  initialValues,
  existingNames = [],
  onSubmit,
  onCancel,
  onTest,
  onAuthorize,
  isTestPending = false,
  isSaving = false,
  testMessage = null,
}: OrgMcpServerFormProps) {
  const { t } = useTranslation();
  const [type, setType] = React.useState<OrgMcpServerType>(
    listing?.type ?? initialValues?.type ?? "shttp",
  );
  const [name, setName] = React.useState(
    listing?.name ?? initialValues?.name ?? "",
  );
  const [url, setUrl] = React.useState(
    listing?.url ?? initialValues?.url ?? "",
  );
  const [timeout, setTimeout] = React.useState(
    listing?.timeout !== undefined ? String(listing.timeout) : "",
  );
  const [command, setCommand] = React.useState(
    listing?.command ?? initialValues?.command ?? "",
  );
  const [args, setArgs] = React.useState(
    (listing?.args ?? initialValues?.args)?.join("\n") ?? "",
  );
  const [env, setEnv] = React.useState(
    formatEnvironmentVariables(listing?.env ?? initialValues?.env),
  );
  const [managedBy, setManagedBy] = React.useState<OrgMcpManagedBy>(
    listing?.managedBy ?? "user",
  );
  const [auth, setAuth] = React.useState<OrgMcpAuthKind>(
    listing?.auth ?? initialValues?.auth ?? "bearer",
  );
  const [headerName, setHeaderName] = React.useState(
    listing?.headerName ?? initialValues?.headerName ?? "",
  );
  const [secretName, setSecretName] = React.useState(
    listing?.secretNames[0] ?? "",
  );
  const [token, setToken] = React.useState("");
  const [oauthClientId, setOAuthClientId] = React.useState(
    listing?.oauth?.clientId ?? "",
  );
  const [oauthClientSecret, setOAuthClientSecret] = React.useState("");
  const [oauthScopes, setOAuthScopes] = React.useState(
    listing?.oauth?.scopes ?? initialValues?.oauthScopes ?? "",
  );
  // The finished consent flow, and the settings it was run with.
  const [oauthAuthorization, setOAuthAuthorization] = React.useState<{
    jobId: string;
    settings: string;
  } | null>(null);
  const [isAuthorizing, setIsAuthorizing] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const isStdio = type === "stdio";
  const parsedArgs = args
    .split("\n")
    .map((arg) => arg.trim())
    .filter(Boolean);
  // A stdio server's stored env values are bound to its command and
  // arguments: the backend restores a masked value only while those are
  // unchanged, the stdio counterpart of the URL rule below.
  const invocationChanged =
    command.trim() !== (listing?.command ?? "") ||
    JSON.stringify(parsedArgs) !== JSON.stringify(listing?.args ?? []);

  // An organization-managed credential comes back masked; leaving the token
  // blank keeps it, but only while the URL is unchanged (the backend restores
  // a masked credential by URL).
  const hasStoredCredential =
    mode === "edit" &&
    listing?.managedBy === "org" &&
    listing.credential === REDACTED_MCP_SECRET;
  const urlChanged = url.trim() !== (listing?.url ?? "");
  const keepStoredCredential =
    hasStoredCredential && managedBy === "org" && !urlChanged && !token;
  // A user-managed credential is each member's own secret: testing would use
  // the admin's secret of that name, if any, not the members' (OHE-3871). The
  // backend cannot test an OAuth server without a consent flow, and a stdio
  // server only runs inside the conversation sandbox.
  const canTestConnection =
    !isStdio && auth !== "oauth" && !(managedBy === "user" && auth !== "none");

  // Same rule for an OAuth client secret. Tokens are tied to the URL and client
  // they were issued for, so changing either needs a new authorization.
  const hasStoredOAuthClientSecret =
    mode === "edit" && listing?.oauth?.clientSecret === REDACTED_MCP_SECRET;
  const keepStoredOAuthClientSecret =
    hasStoredOAuthClientSecret && !urlChanged && !oauthClientSecret;
  const oauthSettings = JSON.stringify([
    type,
    url.trim(),
    oauthClientId.trim(),
    oauthClientSecret,
    oauthScopes.trim(),
  ]);
  const oauthJobId =
    oauthAuthorization?.settings === oauthSettings
      ? oauthAuthorization.jobId
      : undefined;
  const keepsStoredOAuthTokens =
    listing?.managedBy === "org" &&
    !!listing.oauth?.authorized &&
    !urlChanged &&
    oauthClientId.trim() === (listing.oauth.clientId ?? "");
  const isOAuthAuthorized = !!oauthJobId || keepsStoredOAuthTokens;

  const validateUrl = (value: string): string | null => {
    if (!value) return t(I18nKey.SETTINGS$MCP_ERROR_URL_REQUIRED);
    try {
      const parsed = new URL(value);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        return t(I18nKey.SETTINGS$MCP_ERROR_URL_INVALID_PROTOCOL);
      }
    } catch {
      return t(I18nKey.SETTINGS$MCP_ERROR_URL_INVALID);
    }
    return null;
  };

  const validateTimeout = (value: string): string | null => {
    if (!value.trim()) return null;
    const parsed = parseInt(value.trim(), 10);
    if (Number.isNaN(parsed)) {
      return t(I18nKey.SETTINGS$MCP_ERROR_TIMEOUT_INVALID_NUMBER);
    }
    if (parsed <= 0) return t(I18nKey.SETTINGS$MCP_ERROR_TIMEOUT_POSITIVE);
    if (parsed > 3600) {
      return t(I18nKey.SETTINGS$MCP_ERROR_TIMEOUT_MAX_EXCEEDED);
    }
    return null;
  };

  const validateCommand = (value: string): string | null => {
    if (!value) return t(I18nKey.SETTINGS$MCP_ERROR_COMMAND_REQUIRED);
    if (value.includes(" ")) {
      return t(I18nKey.SETTINGS$MCP_ERROR_COMMAND_NO_SPACES);
    }
    return null;
  };

  const validateEnv = (value: string): string | null => {
    const malformed = value.split("\n").some((line) => {
      const trimmed = line.trim();
      if (!trimmed) return false;
      const eq = trimmed.indexOf("=");
      return eq === -1 || !trimmed.substring(0, eq).trim();
    });
    if (malformed) return t(I18nKey.SETTINGS$MCP_ERROR_ENV_INVALID_FORMAT);
    if (
      mode === "edit" &&
      invocationChanged &&
      Object.values(parseEnvironmentVariables(value)).includes(
        REDACTED_MCP_SECRET,
      )
    ) {
      return t(
        I18nKey.SETTINGS$MCP_ORG_ERROR_ENV_REQUIRED_AFTER_COMMAND_CHANGE,
      );
    }
    return null;
  };

  const validate = (): string | null => {
    const trimmedName = name.trim();
    if (mode === "add") {
      if (!trimmedName) return t(I18nKey.SETTINGS$MCP_ERROR_NAME_REQUIRED);
      if (
        !ORG_MCP_SERVER_NAME_PATTERN.test(trimmedName) ||
        RESERVED_NAMES.includes(trimmedName)
      ) {
        return t(I18nKey.SETTINGS$MCP_ERROR_NAME_INVALID);
      }
      if (
        existingNames.some(
          (existing) => existing.toLowerCase() === trimmedName.toLowerCase(),
        )
      ) {
        return t(I18nKey.SETTINGS$MCP_ORG_ERROR_NAME_DUPLICATE);
      }
    }
    if (isStdio) {
      return validateCommand(command.trim()) ?? validateEnv(env);
    }
    const urlError = validateUrl(url.trim());
    if (urlError) return urlError;
    if (type === "shttp") {
      const timeoutError = validateTimeout(timeout);
      if (timeoutError) return timeoutError;
    }
    if (auth === "none") return null;
    if (auth === "oauth") {
      if (hasStoredOAuthClientSecret && urlChanged && !oauthClientSecret) {
        return t(
          I18nKey.SETTINGS$MCP_ORG_ERROR_OAUTH_CLIENT_SECRET_REQUIRED_AFTER_URL_CHANGE,
        );
      }
      return null;
    }
    if (auth === "header" && !headerName.trim()) {
      return t(I18nKey.SETTINGS$MCP_ORG_ERROR_HEADER_NAME_REQUIRED);
    }
    if (managedBy === "user") {
      if (!secretName.trim()) {
        return t(I18nKey.SETTINGS$MCP_ORG_ERROR_SECRET_NAME_REQUIRED);
      }
      if (!SECRET_NAME_PATTERN.test(secretName.trim())) {
        return t(I18nKey.SETTINGS$MCP_ORG_ERROR_SECRET_NAME_INVALID);
      }
      return null;
    }
    if (!token && !keepStoredCredential) {
      return t(
        hasStoredCredential && urlChanged
          ? I18nKey.SETTINGS$MCP_ORG_ERROR_TOKEN_REQUIRED_AFTER_URL_CHANGE
          : I18nKey.SETTINGS$MCP_ORG_ERROR_TOKEN_REQUIRED,
      );
    }
    return null;
  };

  const buildPayload = (): OrgMcpServerCreate => {
    const parsedTimeout = parseInt(timeout.trim(), 10);
    const values: OrgMcpServerFormValues = {
      name: name.trim(),
      managedBy,
      type,
      ...(isStdio
        ? {
            command: command.trim(),
            args: parsedArgs,
            env: parseEnvironmentVariables(env),
          }
        : {
            url: url.trim(),
            ...(type === "shttp" &&
              !Number.isNaN(parsedTimeout) && { timeout: parsedTimeout }),
          }),
      auth: isStdio ? "none" : auth,
      headerName: headerName.trim(),
      secretName: secretName.trim(),
      token,
      keepStoredCredential,
      oauthClientId: oauthClientId.trim(),
      oauthClientSecret,
      keepStoredOAuthClientSecret,
      oauthScopes: oauthScopes.trim(),
      oauthJobId,
    };
    return buildOrgMcpServerPayload(values);
  };

  const runValidated = (action: (payload: OrgMcpServerCreate) => void) => {
    setError(null);
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    action(buildPayload());
  };

  const handleAuthorize = () => {
    runValidated(async (payload) => {
      if (!onAuthorize) return;
      const settings = oauthSettings;
      setIsAuthorizing(true);
      try {
        const result = await onAuthorize(payload);
        if (result.ok) {
          setOAuthAuthorization({ jobId: result.jobId, settings });
        }
      } finally {
        setIsAuthorizing(false);
      }
    });
  };

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    runValidated((payload) => {
      if (
        !isStdio &&
        auth === "oauth" &&
        managedBy === "org" &&
        !isOAuthAuthorized
      ) {
        setError(
          t(I18nKey.SETTINGS$MCP_ORG_ERROR_OAUTH_AUTHORIZATION_REQUIRED),
        );
        return;
      }
      onSubmit(payload);
    });
  };

  const credentialLabel =
    auth === "header"
      ? t(I18nKey.SETTINGS$MCP_ORG_HEADER_VALUE)
      : t(I18nKey.SETTINGS$MCP_ORG_TOKEN);

  return (
    <form
      data-testid={
        mode === "add" ? "add-org-mcp-server-form" : "edit-org-mcp-server-form"
      }
      onSubmit={handleSubmit}
      className="flex w-full flex-col items-stretch gap-6"
    >
      {mode === "add" ? (
        <SettingsDropdownInput
          testId="org-server-type-dropdown"
          name="org-server-type"
          label={t(I18nKey.SETTINGS$MCP_SERVER_TYPE)}
          items={[
            { key: "shttp", label: t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SHTTP) },
            { key: "sse", label: t(I18nKey.SETTINGS$MCP_SERVER_TYPE_SSE) },
            { key: "stdio", label: t(I18nKey.SETTINGS$MCP_SERVER_TYPE_STDIO) },
          ]}
          selectedKey={type}
          onSelectionChange={(key) => {
            if (key) setType(key as OrgMcpServerType);
          }}
          onInputChange={() => {}}
          isClearable={false}
          allowsCustomValue={false}
          required
          wrapperClassName="w-full"
        />
      ) : null}

      {error && (
        <p data-testid="org-mcp-form-error" className="text-red-500 text-sm">
          {error}
        </p>
      )}

      <SettingsInput
        testId="org-name-input"
        name="org-name"
        type="text"
        label={t(I18nKey.SETTINGS$MCP_NAME)}
        className="w-full"
        required
        value={name}
        onChange={setName}
        isReadOnly={mode === "edit"}
        placeholder="my-mcp-server"
      />

      {isStdio && (
        <>
          <SettingsInput
            testId="org-command-input"
            name="org-command"
            type="text"
            label={t(I18nKey.SETTINGS$MCP_COMMAND)}
            className="w-full"
            required
            value={command}
            onChange={setCommand}
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
              data-testid="org-args-input"
              name="org-args"
              rows={3}
              value={args}
              onChange={(event) => setArgs(event.target.value)}
              placeholder="arg1&#10;arg2&#10;arg3"
              className={cn(formControlMultilineFieldClassName, "resize-none")}
            />
            <p className="text-xs text-tertiary-alt">
              {t(I18nKey.SETTINGS$MCP_COMMAND_ARGUMENTS_HELP)}
            </p>
          </label>
        </>
      )}

      {!isStdio && (
        <>
          <SettingsInput
            testId="org-url-input"
            name="org-url"
            type="url"
            label={t(I18nKey.SETTINGS$MCP_URL)}
            className="w-full"
            required
            value={url}
            onChange={setUrl}
            placeholder="https://mcp.example.com/mcp"
          />

          {type === "shttp" && (
            <SettingsInput
              testId="org-timeout-input"
              name="org-timeout"
              type="number"
              label={t(I18nKey.SETTINGS$MCP_TIMEOUT_LABEL)}
              className="w-full"
              showOptionalTag
              value={timeout}
              onChange={setTimeout}
              placeholder="60"
              min={1}
              max={3600}
            />
          )}

          <SettingsDropdownInput
            testId="org-auth-dropdown"
            name="org-auth"
            label={t(I18nKey.SETTINGS$MCP_ORG_AUTH)}
            items={[
              { key: "none", label: t(I18nKey.SETTINGS$MCP_ORG_AUTH_NONE) },
              { key: "bearer", label: t(I18nKey.SETTINGS$MCP_ORG_AUTH_BEARER) },
              { key: "header", label: t(I18nKey.SETTINGS$MCP_ORG_AUTH_HEADER) },
              { key: "oauth", label: t(I18nKey.SETTINGS$MCP_ORG_AUTH_OAUTH) },
            ]}
            selectedKey={auth}
            onSelectionChange={(key) => {
              if (key) setAuth(key as OrgMcpAuthKind);
            }}
            onInputChange={() => {}}
            isClearable={false}
            allowsCustomValue={false}
            required
            wrapperClassName="w-full"
          />

          {auth === "header" && (
            <SettingsInput
              testId="org-header-name-input"
              name="org-header-name"
              type="text"
              label={t(I18nKey.SETTINGS$MCP_ORG_HEADER_NAME)}
              className="w-full"
              required
              value={headerName}
              onChange={setHeaderName}
              placeholder="X-API-Key"
            />
          )}

          {auth === "oauth" && (
            <>
              <SettingsInput
                testId="org-oauth-client-id-input"
                name="org-oauth-client-id"
                type="text"
                label={t(I18nKey.SETTINGS$MCP_ORG_OAUTH_CLIENT_ID)}
                className="w-full"
                showOptionalTag
                value={oauthClientId}
                onChange={setOAuthClientId}
                description={
                  <p className="text-xs text-tertiary-alt">
                    {t(I18nKey.SETTINGS$MCP_ORG_OAUTH_CLIENT_HELP)}
                  </p>
                }
              />
              <SettingsInput
                testId="org-oauth-client-secret-input"
                name="org-oauth-client-secret"
                type="password"
                label={t(I18nKey.SETTINGS$MCP_ORG_OAUTH_CLIENT_SECRET)}
                className="w-full"
                showOptionalTag
                value={oauthClientSecret}
                onChange={setOAuthClientSecret}
                placeholder={
                  keepStoredOAuthClientSecret ? REDACTED_MCP_SECRET : undefined
                }
              />
              <SettingsInput
                testId="org-oauth-scopes-input"
                name="org-oauth-scopes"
                type="text"
                label={t(I18nKey.SETTINGS$MCP_ORG_OAUTH_SCOPES)}
                className="w-full"
                showOptionalTag
                value={oauthScopes}
                onChange={setOAuthScopes}
                placeholder="read write"
                description={
                  <p className="text-xs text-tertiary-alt">
                    {t(I18nKey.SETTINGS$MCP_ORG_OAUTH_SCOPES_HELP)}
                  </p>
                }
              />
            </>
          )}
        </>
      )}

      <SettingsDropdownInput
        testId="org-managed-by-dropdown"
        name="org-managed-by"
        label={t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BY)}
        items={[
          {
            key: "user",
            label:
              auth === "oauth"
                ? t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BY_USER_OAUTH)
                : t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BY_USER),
          },
          { key: "org", label: t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BY_ORG) },
        ]}
        selectedKey={managedBy}
        onSelectionChange={(key) => {
          if (key) setManagedBy(key as OrgMcpManagedBy);
        }}
        onInputChange={() => {}}
        isClearable={false}
        allowsCustomValue={false}
        required
        wrapperClassName="w-full"
      />

      {isStdio && (
        <label className="flex w-full flex-col gap-2.5">
          <div className="flex items-center gap-2">
            <span className="text-sm">
              {t(I18nKey.SETTINGS$MCP_ENVIRONMENT_VARIABLES)}
            </span>
            <OptionalTag />
          </div>
          <textarea
            data-testid="org-env-input"
            name="org-env"
            rows={4}
            value={env}
            onChange={(event) => setEnv(event.target.value)}
            placeholder="KEY1=value1&#10;KEY2=value2"
            className={cn(formControlMultilineFieldClassName, "resize-none")}
          />
          <p className="text-xs text-tertiary-alt">
            {managedBy === "user"
              ? t(I18nKey.SETTINGS$MCP_ORG_ENV_USER_HELP)
              : t(I18nKey.SETTINGS$MCP_ORG_ENV_ORG_HELP)}
          </p>
        </label>
      )}

      {!isStdio && (
        <>
          {auth === "oauth" &&
            (managedBy === "user" ? (
              <p className="text-xs text-tertiary-alt">
                {t(I18nKey.SETTINGS$MCP_ORG_OAUTH_USER_HELP)}
              </p>
            ) : (
              <div className="flex items-center gap-3">
                <BrandButton
                  testId="org-oauth-authorize-button"
                  type="button"
                  variant="secondary"
                  onClick={handleAuthorize}
                  isDisabled={isAuthorizing}
                  className="shrink-0"
                >
                  {isAuthorizing
                    ? t(I18nKey.SETTINGS$MCP_ORG_OAUTH_AUTHORIZING)
                    : t(I18nKey.SETTINGS$MCP_ORG_OAUTH_AUTHORIZE)}
                </BrandButton>
                {isOAuthAuthorized ? (
                  <span
                    data-testid="org-oauth-authorized"
                    className="text-xs text-success"
                  >
                    {t(I18nKey.SETTINGS$MCP_ORG_OAUTH_AUTHORIZED)}
                  </span>
                ) : (
                  <p className="text-xs text-tertiary-alt">
                    {t(I18nKey.SETTINGS$MCP_ORG_OAUTH_ORG_HELP)}
                  </p>
                )}
              </div>
            ))}

          {(auth === "bearer" || auth === "header") && managedBy === "user" && (
            <SettingsInput
              testId="org-secret-name-input"
              name="org-secret-name"
              type="text"
              label={t(I18nKey.SETTINGS$MCP_ORG_SECRET_NAME)}
              className="w-full"
              required
              value={secretName}
              onChange={setSecretName}
              placeholder="MY_MCP_TOKEN"
              description={
                <p className="text-xs text-tertiary-alt">
                  {t(I18nKey.SETTINGS$MCP_ORG_SECRET_NAME_HELP)}
                </p>
              }
            />
          )}

          {(auth === "bearer" || auth === "header") && managedBy === "org" && (
            <SettingsInput
              testId="org-token-input"
              name="org-token"
              type="password"
              label={credentialLabel}
              className="w-full"
              required={!hasStoredCredential}
              value={token}
              onChange={setToken}
              placeholder={
                keepStoredCredential || (hasStoredCredential && !urlChanged)
                  ? REDACTED_MCP_SECRET
                  : t(I18nKey.SETTINGS$MCP_API_KEY_PLACEHOLDER)
              }
              description={
                <p className="text-xs text-tertiary-alt">
                  {t(I18nKey.SETTINGS$MCP_ORG_TOKEN_HELP)}
                </p>
              }
            />
          )}
        </>
      )}

      {testMessage && canTestConnection && (
        <p
          data-testid="org-mcp-test-message"
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
          testId="org-cancel-button"
          type="button"
          variant="secondary"
          onClick={onCancel}
        >
          {t(I18nKey.BUTTON$CANCEL)}
        </BrandButton>
        {onTest && canTestConnection && (
          <BrandButton
            testId="org-mcp-test-connection"
            type="button"
            variant="secondary"
            onClick={() => runValidated(onTest)}
            isDisabled={isTestPending}
          >
            {isTestPending
              ? t(I18nKey.SETTINGS$MCP_TESTING)
              : t(I18nKey.SETTINGS$MCP_TEST_CONNECTION)}
          </BrandButton>
        )}
        <BrandButton
          testId="org-submit-button"
          type="submit"
          variant="primary"
          isDisabled={isTestPending || isSaving || isAuthorizing}
        >
          {mode === "add"
            ? t(I18nKey.SETTINGS$MCP_ORG_ADD_SERVER)
            : t(I18nKey.SETTINGS$MCP_SAVE_SERVER)}
        </BrandButton>
      </div>
    </form>
  );
}
