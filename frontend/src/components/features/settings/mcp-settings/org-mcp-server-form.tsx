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
  OrgMcpServerType,
  OrgMcpServerView,
  REDACTED_MCP_SECRET,
  SECRET_NAME_PATTERN,
  buildOrgMcpServerPayload,
} from "#/utils/org-mcp-servers";
import { SettingsInput } from "../settings-input";
import { SettingsDropdownInput } from "../settings-dropdown-input";
import { BrandButton } from "../brand-button";
import { cn } from "#/utils/utils";
import type { TestMessage } from "./mcp-server-form";

/** The system OpenHands MCP server is injected under this name at launch. */
const RESERVED_NAMES = ["default"];

interface OrgMcpServerFormProps {
  mode: "add" | "edit";
  listing?: OrgMcpServerView;
  /** Names of the organization's other listings (uniqueness when adding). */
  existingNames?: string[];
  onSubmit: (payload: OrgMcpServerCreate) => void;
  onCancel: () => void;
  onTest?: (payload: OrgMcpServerCreate) => void;
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
 * creates to connect.
 */
export function OrgMcpServerForm({
  mode,
  listing,
  existingNames = [],
  onSubmit,
  onCancel,
  onTest,
  isTestPending = false,
  isSaving = false,
  testMessage = null,
}: OrgMcpServerFormProps) {
  const { t } = useTranslation();
  const [type, setType] = React.useState<OrgMcpServerType>(
    listing?.type ?? "shttp",
  );
  const [name, setName] = React.useState(listing?.name ?? "");
  const [url, setUrl] = React.useState(listing?.url ?? "");
  const [timeout, setTimeout] = React.useState(
    listing?.timeout !== undefined ? String(listing.timeout) : "",
  );
  const [managedBy, setManagedBy] = React.useState<OrgMcpManagedBy>(
    listing?.managedBy ?? "user",
  );
  const [auth, setAuth] = React.useState<OrgMcpAuthKind>(
    listing?.auth ?? "bearer",
  );
  const [headerName, setHeaderName] = React.useState(listing?.headerName ?? "");
  const [secretName, setSecretName] = React.useState(
    listing?.secretNames[0] ?? "",
  );
  const [token, setToken] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

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
  // the admin's secret of that name, if any, not the members' (OHE-3871).
  const canTestConnection = !(managedBy === "user" && auth !== "none");

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
    const urlError = validateUrl(url.trim());
    if (urlError) return urlError;
    if (type === "shttp") {
      const timeoutError = validateTimeout(timeout);
      if (timeoutError) return timeoutError;
    }
    if (auth === "none") return null;
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
      url: url.trim(),
      ...(type === "shttp" &&
        !Number.isNaN(parsedTimeout) && { timeout: parsedTimeout }),
      auth,
      headerName: headerName.trim(),
      secretName: secretName.trim(),
      token,
      keepStoredCredential,
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

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    runValidated(onSubmit);
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

      <SettingsDropdownInput
        testId="org-managed-by-dropdown"
        name="org-managed-by"
        label={t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BY)}
        items={[
          { key: "user", label: t(I18nKey.SETTINGS$MCP_ORG_MANAGED_BY_USER) },
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

      {auth !== "none" && managedBy === "user" && (
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

      {auth !== "none" && managedBy === "org" && (
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
          isDisabled={isTestPending || isSaving}
        >
          {mode === "add"
            ? t(I18nKey.SETTINGS$MCP_ORG_ADD_SERVER)
            : t(I18nKey.SETTINGS$MCP_SAVE_SERVER)}
        </BrandButton>
      </div>
    </form>
  );
}
