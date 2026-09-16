/* eslint-disable i18next/no-literal-string */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import { Link } from "react-router";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleDashed,
  Clock,
  RefreshCw,
  Settings2,
} from "lucide-react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { StyledTooltip } from "#/components/shared/buttons/styled-tooltip";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { HubBadge } from "#/components/features/integrations-hub/hub-badge";
import { HubFilterTabs } from "#/components/features/integrations-hub/hub-filter-tabs";
import { HubIntegrationModalHeader } from "#/components/features/integrations-hub/hub-integration-modal-header";
import { HubIntegrationEnableRow } from "#/components/features/integrations-hub/integration-detail-modal";
import { ConnectorSetupProgress } from "#/components/features/integrations-hub/connector-setup-progress";
import { HubToolAccessList } from "#/components/features/integrations-hub/hub-tool-access-list";
import {
  hubModalFooterClassName,
  hubModalShellClassName,
  HubModalCloseButton,
} from "#/components/features/integrations-hub/hub-modal";
import { OFFICIAL_HUB_CATALOG } from "#/components/features/integrations-hub/hub-official-catalog";
import { INTEGRATIONS_HUB_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";
import type {
  HubIntegration,
  HubTool,
  HubToolAccessMode,
} from "#/types/integrations-hub";
import {
  formControlFieldClassName,
  formControlNativeSelectClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

type RecipeId = "current" | "tools-first" | "tabs" | "wizard";
type VariantId = RecipeId | "custom";
type PhaseId = "first-run" | "after-setup";
type SetupPane = "configure" | "connect" | "tools";

const GITHUB_CATALOG = OFFICIAL_HUB_CATALOG.find(
  (item) => item.slug === "github",
)!;

const INDEXED_TOOLS: HubTool[] = [
  {
    name: "create_issue",
    description: "Open a GitHub issue in the selected repository.",
    accessMode: "approval",
    defaultScopes: ["issues:write"],
  },
  {
    name: "request_review",
    description: "Request review on a pull request.",
    accessMode: "approval",
    defaultScopes: ["pull_requests:write"],
  },
  {
    name: "search_repos",
    description: "Search repositories the user can access.",
    accessMode: "enabled",
    defaultScopes: ["repo:read"],
  },
];

const SETUP_PANE_LABEL: Record<SetupPane, string> = {
  configure: "Configure",
  connect: "Connect",
  tools: "Index tools",
};

const WIZARD_PRIMARY_LABEL: Record<SetupPane, string> = {
  configure: "Register and continue",
  connect: "Connect and continue",
  tools: "Finish",
};

const REGISTER_BUTTON_LABEL: Record<
  "saved" | "idle" | "success" | "error",
  string
> = {
  saved: "Save configuration",
  idle: "Register connector",
  success: "Registered",
  error: "Register connector",
};

type ConnectMethod = "oauth" | "pat" | "api_key";

const CONNECT_METHODS: Array<{ value: ConnectMethod; label: string }> = [
  { value: "oauth", label: "OAuth" },
  { value: "pat", label: "Personal access token" },
  { value: "api_key", label: "API key" },
];

const CONNECT_METHOD_HELP: Record<ConnectMethod, string> = {
  oauth:
    "Sign in with the provider to authorize this deployment and verify the connector works.",
  pat: "Paste a personal access token. The connector sends it as a bearer token.",
  api_key: "Paste the provider API key this connector should use upstream.",
};

const CONNECT_BUTTON_LABEL: Record<
  "idle" | "success" | "error" | "connected",
  string
> = {
  idle: "Connect",
  success: "Connected",
  error: "Connect",
  connected: "Reconnect",
};

const CONNECT_ERROR_MESSAGE: Record<ConnectMethod, string> = {
  oauth: "Authorization was denied. Try connecting again.",
  pat: "Enter a personal access token to connect.",
  api_key: "Enter an API key to connect.",
};

const INVALID_FIELD_CLASS_NAME =
  "border-[var(--oh-color-danger)] bg-[color:rgba(231,106,94,0.08)] focus:border-[var(--oh-color-danger)] focus:ring-[color:rgba(231,106,94,0.35)]";

const INDEX_TOOLS_DELAY_MS = 800;

const VARIANTS: Array<{
  value: VariantId;
  label: string;
  recommended?: boolean;
}> = [
  { value: "current", label: "Current stepper" },
  { value: "tools-first", label: "Tools-first", recommended: true },
  { value: "tabs", label: "Tabs" },
  { value: "wizard", label: "Wizard, then manage" },
  { value: "custom", label: "Custom" },
];

const VARIANT_NOTES: Record<
  VariantId,
  { firstRun: string; afterSetup: string; why: string }
> = {
  current: {
    why: "Same stacked setup cards as today. Completed steps stay expanded when the modal is reopened, so Index tools sits below the fold.",
    firstRun:
      "Configure, Connect, and Index are all visible. The form is the work.",
    afterSetup:
      "The same wizard is still the page. Index tools — the actual job — is buried under credentials you already saved.",
  },
  "tools-first": {
    why: "Matches our user Integration detail modal and industry install-then-manage: setup is a focused first-run, then the modal becomes a tools surface with Settings one click away.",
    firstRun:
      "Only the current setup task is on screen. Connection and indexing wait until this form is saved.",
    afterSetup:
      "Tools fill the body. A compact health strip replaces the stepper. Connector settings is a secondary view, not the default.",
  },
  tabs: {
    why: "Same pattern as Auth0 Applications, GitHub App settings, and our Hub filter tabs: one object, sibling sections, default tab follows the job.",
    firstRun: "Opens on Configuration so first-time setup is obvious.",
    afterSetup:
      "Opens on Tools. Configuration and Connection stay reachable without making you scroll past them.",
  },
  wizard: {
    why: "NN/g staged disclosure: a linear wizard only while steps depend on each other. After Finish, never show the wizard again.",
    firstRun:
      "One step at a time with Next / Back. Later steps stay locked until prerequisites pass.",
    afterSetup:
      "The wizard is gone. You land on the same tools-first manage surface as variant 2.",
  },
  custom: {
    why: "Chosen first-run is the focused tools-first configure card. After-setup is still a placeholder until that manage surface is specified.",
    firstRun:
      "One setup card at a time: credentials first, then Connect. Tools are indexed automatically after a successful connection.",
    afterSetup: "Placeholder after-setup. Waiting for the manage end result.",
  },
};

function createGithub(phase: PhaseId, tools: HubTool[]): HubIntegration {
  const afterSetup = phase === "after-setup";
  return {
    ...GITHUB_CATALOG,
    connected: afterSetup,
    enabled: afterSetup,
    tools: afterSetup ? tools : [],
    toolCount: afterSetup ? tools.length : 0,
    updatedAt: "2026-09-16T14:31:53.000Z",
  };
}

const UPDATED_AT_LABEL = "Updated 9/16/2026, 7:31:53 AM";

function PreviewModal({
  onClose,
  children,
  footer,
  testId,
  showUpdated = true,
}: {
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
  testId: string;
  showUpdated?: boolean;
}) {
  return (
    <section
      data-testid={testId}
      className={cn(hubModalShellClassName("xl"), "min-h-0 max-h-[90vh] gap-0")}
    >
      <HubModalCloseButton onClose={onClose} testId={`${testId}-close`} />
      {children}
      <div
        className={cn(
          hubModalFooterClassName,
          showUpdated ? "justify-between" : "justify-end",
        )}
      >
        {showUpdated ? (
          <p className="inline-flex items-center gap-1.5 text-[11px] leading-5 text-[var(--oh-text-secondary)]">
            <Clock className="h-3.5 w-3.5 shrink-0" aria-hidden />
            {UPDATED_AT_LABEL}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">{footer}</div>
      </div>
    </section>
  );
}

function ConnectorHeader({
  integration,
  children,
}: {
  integration: HubIntegration;
  children?: ReactNode;
}) {
  return (
    <div className="shrink-0 border-b border-[var(--oh-border)] px-7 pb-4 pt-7">
      <div className="pr-5">
        <HubIntegrationModalHeader integration={integration} showDescription />
      </div>
      {children}
    </div>
  );
}

function ConfigForm({
  registered,
  onRegister,
  confirmBeforeContinue = false,
  confirmed = false,
  hidePrimaryAction = false,
  formRef,
  onConfirmedChange,
}: {
  registered: boolean;
  onRegister: () => void;
  confirmBeforeContinue?: boolean;
  confirmed?: boolean;
  hidePrimaryAction?: boolean;
  formRef?: Ref<HTMLFormElement>;
  onConfirmedChange?: (confirmed: boolean) => void;
}) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [provider, setProvider] = useState<"mcp" | "http">("mcp");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [status, setStatus] = useState<"idle" | "success" | "error">(
    confirmed ? "success" : "idle",
  );

  const updateStatus = (
    nextStatus: "idle" | "success" | "error",
    nextConfirmed: boolean,
  ) => {
    setStatus(nextStatus);
    onConfirmedChange?.(nextConfirmed);
  };

  const handleCredentialChange = (
    setter: (value: string) => void,
    value: string,
  ) => {
    setter(value);
    if (confirmBeforeContinue && status !== "idle") {
      updateStatus("idle", false);
    }
  };

  const clientIdInvalid = status === "error" && !clientId.trim();
  const clientSecretInvalid = status === "error" && !clientSecret.trim();

  return (
    <form
      ref={formRef}
      className="grid gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!confirmBeforeContinue) {
          onRegister();
          return;
        }
        if (clientId.trim() && clientSecret.trim()) {
          updateStatus("success", true);
          onRegister();
          return;
        }
        updateStatus("error", false);
      }}
    >
      <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
        Provider type
        <select
          className={formControlNativeSelectClassName}
          value={provider}
          onChange={(event) =>
            setProvider(event.target.value as "mcp" | "http")
          }
        >
          <option value="mcp">Remote MCP</option>
          <option value="http">Managed HTTP / OpenAPI</option>
        </select>
      </label>
      <div>
        <button
          type="button"
          data-testid="tools-first-advanced"
          onClick={() => setAdvancedOpen((current) => !current)}
          className="inline-flex items-center gap-1 text-xs font-medium text-muted hover:text-[var(--oh-text-secondary)]"
          aria-expanded={advancedOpen}
        >
          {advancedOpen ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          Advanced
        </button>
        {advancedOpen ? (
          <div className="mt-3 grid gap-3">
            {provider === "http" ? (
              <>
                <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
                  API base URL
                  <input
                    className={formControlFieldClassName}
                    type="url"
                    defaultValue={GITHUB_CATALOG.apiBaseUrl}
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
                  OpenAPI URL
                  <input
                    className={formControlFieldClassName}
                    type="url"
                    placeholder="https://developers.example.com/openapi.json"
                    defaultValue={GITHUB_CATALOG.openApiUrl}
                  />
                </label>
              </>
            ) : (
              <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
                MCP server URL
                <input
                  className={formControlFieldClassName}
                  type="url"
                  defaultValue={GITHUB_CATALOG.serverUrl}
                />
              </label>
            )}
            <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
              Authorization URL
              <input
                className={formControlFieldClassName}
                type="url"
                defaultValue={GITHUB_CATALOG.oauthConfig?.authorizationUrl}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
              Token URL
              <input
                className={formControlFieldClassName}
                type="url"
                defaultValue={GITHUB_CATALOG.oauthConfig?.tokenUrl}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
              Required scopes
              <input
                className={formControlFieldClassName}
                type="text"
                defaultValue={(GITHUB_CATALOG.oauthConfig?.scopes ?? []).join(
                  ", ",
                )}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]">
              Optional scopes
              <input
                className={formControlFieldClassName}
                type="text"
                placeholder="contacts.write, settings.read"
                defaultValue={(
                  GITHUB_CATALOG.oauthConfig?.optionalScopes ?? []
                ).join(", ")}
              />
            </label>
          </div>
        ) : null}
      </div>
      <label
        className={cn(
          "flex flex-col gap-1 text-xs",
          clientIdInvalid
            ? "text-[var(--oh-color-danger)]"
            : "text-[var(--oh-text-secondary)]",
        )}
      >
        OAuth client ID
        <input
          className={cn(
            formControlFieldClassName,
            clientIdInvalid && INVALID_FIELD_CLASS_NAME,
          )}
          type="text"
          value={clientId}
          onChange={(event) =>
            handleCredentialChange(setClientId, event.target.value)
          }
          aria-invalid={clientIdInvalid}
          aria-describedby={
            confirmBeforeContinue && status === "error"
              ? "tools-first-register-error"
              : undefined
          }
          data-testid="tools-first-client-id"
        />
      </label>
      <label
        className={cn(
          "flex flex-col gap-1 text-xs",
          clientSecretInvalid
            ? "text-[var(--oh-color-danger)]"
            : "text-[var(--oh-text-secondary)]",
        )}
      >
        OAuth client secret
        <input
          className={cn(
            formControlFieldClassName,
            clientSecretInvalid && INVALID_FIELD_CLASS_NAME,
          )}
          type="password"
          value={clientSecret}
          onChange={(event) =>
            handleCredentialChange(setClientSecret, event.target.value)
          }
          aria-invalid={clientSecretInvalid}
          aria-describedby={
            confirmBeforeContinue && status === "error"
              ? "tools-first-register-error"
              : undefined
          }
          data-testid="tools-first-client-secret"
        />
      </label>
      <div className="rounded-lg border border-[var(--oh-border)] bg-[var(--oh-bg-input)] px-3 py-2 text-[11px] leading-5 text-[var(--oh-text-secondary)]">
        Set the redirect URI to http://localhost:12000/api/oauth/github/callback
        for this deployment.
      </div>
      <div className="space-y-2">
        {hidePrimaryAction ? null : (
          <BrandButton
            type="submit"
            variant={status === "success" ? "secondary" : "primary"}
            testId={
              registered
                ? "tools-first-save-config"
                : "tools-first-register-connector"
            }
            startContent={
              status === "success" ? (
                <CheckCircle2 className="h-4 w-4 text-[var(--oh-color-success)]" />
              ) : undefined
            }
          >
            {REGISTER_BUTTON_LABEL[registered ? "saved" : status]}
          </BrandButton>
        )}
        {confirmBeforeContinue && status === "error" ? (
          <p
            id="tools-first-register-error"
            className="flex w-full items-start gap-1.5 rounded-lg border border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.1)] py-2 pl-4 pr-3 text-xs leading-5 text-[var(--oh-color-danger)]"
            data-testid="tools-first-register-error"
            role="alert"
          >
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            Enter an OAuth client ID and secret to register this connector.
          </p>
        ) : null}
      </div>
    </form>
  );
}

function HealthStrip() {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <HubBadge size="sm" tone="success">
        Configured
      </HubBadge>
      <HubBadge size="sm" tone="success">
        Connected
      </HubBadge>
      <HubBadge size="sm" tone="success">
        Indexed
      </HubBadge>
    </div>
  );
}

function ToolsPane({
  tools,
  isIndexing = false,
  onIndex,
  onUpdateToolAccess,
}: {
  tools: HubTool[];
  isIndexing?: boolean;
  onIndex?: () => void;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  const indexButton = (
    <StyledTooltip content="Refresh list" placement="top">
      <BrandButton
        type="button"
        variant="secondary"
        testId="tools-first-index-tools"
        ariaLabel="Refresh list"
        isDisabled={isIndexing}
        className="size-9 min-h-9 px-0"
        startContent={
          <RefreshCw className={cn("h-4 w-4", isIndexing && "animate-spin")} />
        }
        onClick={onIndex}
      />
    </StyledTooltip>
  );

  if (isIndexing) {
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        <div className="flex items-center justify-end gap-2">{indexButton}</div>
        <div
          className="min-h-0 max-h-[min(22rem,50vh)] flex-1 overflow-y-auto overflow-x-hidden rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)]"
          data-testid="tools-first-index-loading"
        >
          <div className="flex min-h-[12rem] flex-col items-center justify-center gap-3 px-4 py-8">
            <LoadingSpinner size="small" />
            <p className="text-sm text-[var(--oh-text-secondary)]">
              Indexing tools…
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (tools.length === 0) {
    return <div className="flex items-center justify-end">{indexButton}</div>;
  }

  return (
    <HubToolAccessList
      tools={tools}
      searchTestId="connector-variants-tools-search"
      toolbarEnd={indexButton}
      onUpdateToolAccess={onUpdateToolAccess}
    />
  );
}

function ConnectPane({
  method = "oauth",
  connected,
  onConnect,
  showDescription = true,
  confirmBeforeContinue = false,
  confirmed = false,
  hidePrimaryAction = false,
  formRef,
  onConfirmedChange,
}: {
  method?: ConnectMethod;
  connected: boolean;
  onConnect?: () => void;
  showDescription?: boolean;
  confirmBeforeContinue?: boolean;
  confirmed?: boolean;
  hidePrimaryAction?: boolean;
  formRef?: Ref<HTMLFormElement>;
  onConfirmedChange?: (confirmed: boolean) => void;
}) {
  const [token, setToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [status, setStatus] = useState<"idle" | "success" | "error">(
    confirmed || connected ? "success" : "idle",
  );

  const updateStatus = (
    nextStatus: "idle" | "success" | "error",
    nextConfirmed: boolean,
  ) => {
    setStatus(nextStatus);
    onConfirmedChange?.(nextConfirmed);
  };

  useEffect(() => {
    setToken("");
    setApiKey("");
    if (confirmBeforeContinue) {
      setStatus("idle");
      onConfirmedChange?.(false);
    }
  }, [method, confirmBeforeContinue, onConfirmedChange]);

  const handleSecretChange = (
    setter: (value: string) => void,
    value: string,
  ) => {
    setter(value);
    if (confirmBeforeContinue && status !== "idle") {
      updateStatus("idle", false);
    }
  };

  const tokenInvalid = status === "error" && method === "pat" && !token.trim();
  const apiKeyInvalid =
    status === "error" && method === "api_key" && !apiKey.trim();
  const canConnect =
    method === "oauth" ||
    (method === "pat" && Boolean(token.trim())) ||
    (method === "api_key" && Boolean(apiKey.trim()));

  if (!confirmBeforeContinue) {
    return (
      <div className="space-y-3">
        {showDescription ? (
          <p className="text-xs leading-5 text-[var(--oh-text-secondary)]">
            {CONNECT_METHOD_HELP[method]}
          </p>
        ) : null}
        <BrandButton
          type="button"
          variant="primary"
          testId="tools-first-connect"
          onClick={onConnect}
        >
          {connected
            ? CONNECT_BUTTON_LABEL.connected
            : CONNECT_BUTTON_LABEL.idle}
        </BrandButton>
      </div>
    );
  }

  return (
    <form
      ref={formRef}
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (canConnect) {
          updateStatus("success", true);
          onConnect?.();
          return;
        }
        updateStatus("error", false);
      }}
    >
      {showDescription ? (
        <p className="text-xs leading-5 text-[var(--oh-text-secondary)]">
          {CONNECT_METHOD_HELP[method]}
        </p>
      ) : null}
      {method === "pat" ? (
        <label
          className={cn(
            "flex flex-col gap-1 text-xs",
            tokenInvalid
              ? "text-[var(--oh-color-danger)]"
              : "text-[var(--oh-text-secondary)]",
          )}
        >
          Personal access token
          <input
            className={cn(
              formControlFieldClassName,
              tokenInvalid && INVALID_FIELD_CLASS_NAME,
            )}
            type="password"
            value={token}
            onChange={(event) =>
              handleSecretChange(setToken, event.target.value)
            }
            aria-invalid={tokenInvalid}
            aria-describedby={
              status === "error" ? "tools-first-connect-error" : undefined
            }
            data-testid="tools-first-connect-token"
          />
        </label>
      ) : null}
      {method === "api_key" ? (
        <label
          className={cn(
            "flex flex-col gap-1 text-xs",
            apiKeyInvalid
              ? "text-[var(--oh-color-danger)]"
              : "text-[var(--oh-text-secondary)]",
          )}
        >
          API key
          <input
            className={cn(
              formControlFieldClassName,
              apiKeyInvalid && INVALID_FIELD_CLASS_NAME,
            )}
            type="password"
            value={apiKey}
            onChange={(event) =>
              handleSecretChange(setApiKey, event.target.value)
            }
            aria-invalid={apiKeyInvalid}
            aria-describedby={
              status === "error" ? "tools-first-connect-error" : undefined
            }
            data-testid="tools-first-connect-api-key"
          />
        </label>
      ) : null}
      <div className="space-y-2">
        {hidePrimaryAction ? null : (
          <BrandButton
            type="submit"
            variant={status === "success" ? "secondary" : "primary"}
            testId="tools-first-connect"
            startContent={
              status === "success" ? (
                <CheckCircle2 className="h-4 w-4 text-[var(--oh-color-success)]" />
              ) : undefined
            }
          >
            {CONNECT_BUTTON_LABEL[status]}
          </BrandButton>
        )}
        {status === "error" ? (
          <p
            id="tools-first-connect-error"
            className="flex w-full items-start gap-1.5 rounded-lg border border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.1)] py-2 pl-4 pr-3 text-xs leading-5 text-[var(--oh-color-danger)]"
            data-testid="tools-first-connect-error"
            role="alert"
          >
            <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {CONNECT_ERROR_MESSAGE[method]}
          </p>
        ) : null}
      </div>
    </form>
  );
}

function SetupCard({
  kicker,
  title,
  description,
  children,
}: {
  kicker?: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] p-4">
      {kicker ? (
        <p className="text-xs font-medium text-muted">{kicker}</p>
      ) : null}
      <h3
        className={cn(
          "text-sm font-semibold text-white",
          kicker ? "mt-0.5" : null,
        )}
      >
        {title}
      </h3>
      <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
        {description}
      </p>
      <div className="mt-4">{children}</div>
    </div>
  );
}

function ToolsFirstModal({
  phase,
  integration,
  tools,
  connectMethod,
  onClose,
  onRegister,
  onUpdateToolAccess,
  hideConfiguredBadge = false,
}: {
  phase: PhaseId;
  integration: HubIntegration;
  tools: HubTool[];
  connectMethod: ConnectMethod;
  onClose: () => void;
  onRegister: () => void;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
  hideConfiguredBadge?: boolean;
}) {
  const [view, setView] = useState<"work" | "settings">(
    phase === "after-setup" ? "work" : "settings",
  );
  const [setupStep, setSetupStep] = useState<SetupPane>("configure");
  const [indexedTools, setIndexedTools] = useState<HubTool[]>([]);
  const [isIndexing, setIsIndexing] = useState(false);
  const [configureReady, setConfigureReady] = useState(false);
  const [connectReady, setConnectReady] = useState(false);
  const configFormRef = useRef<HTMLFormElement>(null);
  const connectFormRef = useRef<HTMLFormElement>(null);
  const indexTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runIndex = () => {
    if (indexTimeoutRef.current) {
      clearTimeout(indexTimeoutRef.current);
    }
    setIsIndexing(true);
    setIndexedTools([]);
    indexTimeoutRef.current = setTimeout(() => {
      setIndexedTools(tools);
      setIsIndexing(false);
      indexTimeoutRef.current = null;
    }, INDEX_TOOLS_DELAY_MS);
  };

  useEffect(
    () => () => {
      if (indexTimeoutRef.current) {
        clearTimeout(indexTimeoutRef.current);
      }
    },
    [],
  );

  const goNext = () => {
    if (setupStep === "configure") {
      configFormRef.current?.requestSubmit();
      return;
    }
    if (setupStep === "connect" && connectReady) {
      setSetupStep("tools");
      runIndex();
    }
  };

  if (phase === "first-run") {
    return (
      <PreviewModal
        testId="connector-variant-tools-first"
        onClose={onClose}
        showUpdated={false}
        footer={
          <>
            {setupStep === "configure" ? (
              <BrandButton
                type="button"
                variant="secondary"
                testId="tools-first-cancel"
                onClick={onClose}
              >
                Cancel
              </BrandButton>
            ) : (
              <BrandButton
                type="button"
                variant="secondary"
                onClick={() =>
                  setSetupStep(setupStep === "tools" ? "connect" : "configure")
                }
              >
                Back
              </BrandButton>
            )}
            {setupStep === "tools" ? (
              <BrandButton
                type="button"
                variant="primary"
                testId="tools-first-done"
                isDisabled={isIndexing}
                onClick={onClose}
              >
                Done
              </BrandButton>
            ) : (
              <BrandButton
                type="button"
                variant="primary"
                testId="tools-first-next"
                isDisabled={setupStep === "connect" && !connectReady}
                onClick={goNext}
              >
                Next
              </BrandButton>
            )}
          </>
        }
      >
        <ConnectorHeader integration={integration} />
        <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
          {setupStep === "configure" ? (
            <SetupCard
              title="Configure integration"
              description="Enter the OAuth client ID and secret from the provider so this deployment can register the connector."
            >
              <ConfigForm
                registered={false}
                confirmBeforeContinue
                hidePrimaryAction
                confirmed={configureReady}
                formRef={configFormRef}
                onConfirmedChange={setConfigureReady}
                onRegister={() => {
                  setConfigureReady(true);
                  onRegister();
                  setSetupStep("connect");
                }}
              />
            </SetupCard>
          ) : null}
          {setupStep === "connect" ? (
            <SetupCard
              title="Connect your account"
              description={CONNECT_METHOD_HELP[connectMethod]}
            >
              <ConnectPane
                method={connectMethod}
                connected={false}
                showDescription={false}
                confirmBeforeContinue
                confirmed={connectReady}
                formRef={connectFormRef}
                onConfirmedChange={setConnectReady}
              />
            </SetupCard>
          ) : null}
          {setupStep === "tools" ? (
            <ToolsPane
              tools={indexedTools}
              isIndexing={isIndexing}
              onIndex={runIndex}
              onUpdateToolAccess={onUpdateToolAccess}
            />
          ) : null}
        </div>
      </PreviewModal>
    );
  }

  return (
    <PreviewModal
      testId="connector-variant-tools-first"
      onClose={onClose}
      footer={
        <>
          {view === "work" ? (
            <BrandButton
              type="button"
              variant="secondary"
              startContent={<Settings2 className="h-4 w-4" />}
              onClick={() => setView("settings")}
            >
              Connector settings
            </BrandButton>
          ) : (
            <BrandButton
              type="button"
              variant="secondary"
              onClick={() => setView("work")}
            >
              Back to tools
            </BrandButton>
          )}
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            Close
          </BrandButton>
        </>
      }
    >
      <ConnectorHeader integration={integration}>
        <HubIntegrationEnableRow
          integration={integration}
          onToggleEnabled={() => undefined}
        />
      </ConnectorHeader>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
        {view === "work" ? (
          <div className="space-y-4">
            {hideConfiguredBadge ? null : (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <HealthStrip />
              </div>
            )}
            <ToolsPane
              tools={tools}
              isIndexing={isIndexing}
              onIndex={runIndex}
              onUpdateToolAccess={onUpdateToolAccess}
            />
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-white">
                Connector settings
              </h3>
              <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
                Rare changes: endpoints, client credentials, and reconnect.
              </p>
            </div>
            <ConfigForm registered onRegister={onRegister} />
            <div className="rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] p-4">
              <h4 className="text-sm font-semibold text-white">
                Connected account
              </h4>
              <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
                Connected as you.
              </p>
              <div className="mt-3">
                <ConnectPane method={connectMethod} connected />
              </div>
            </div>
          </div>
        )}
      </div>
    </PreviewModal>
  );
}

function TabsModal({
  phase,
  integration,
  tools,
  connectMethod,
  onClose,
  onRegister,
  onUpdateToolAccess,
}: {
  phase: PhaseId;
  integration: HubIntegration;
  tools: HubTool[];
  connectMethod: ConnectMethod;
  onClose: () => void;
  onRegister: () => void;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  const [tab, setTab] = useState<SetupPane>(
    phase === "after-setup" ? "tools" : "configure",
  );

  return (
    <PreviewModal
      testId="connector-variant-tabs"
      onClose={onClose}
      showUpdated={phase === "after-setup"}
      footer={
        <BrandButton type="button" variant="secondary" onClick={onClose}>
          Close
        </BrandButton>
      }
    >
      <ConnectorHeader integration={integration} />
      <div className="shrink-0 border-b border-[var(--oh-border)] px-7">
        <HubFilterTabs
          value={tab}
          onChange={setTab}
          ariaLabel="Connector sections"
          testId="connector-section-tabs"
          options={[
            {
              value: "tools",
              label: "Tools",
              count: phase === "after-setup" ? tools.length : undefined,
            },
            { value: "connect", label: "Connection" },
            { value: "configure", label: "Configuration" },
          ]}
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
        {tab === "tools" && phase === "after-setup" ? (
          <ToolsPane tools={tools} onUpdateToolAccess={onUpdateToolAccess} />
        ) : null}
        {tab === "tools" && phase === "first-run" ? (
          <p className="text-sm leading-6 text-tertiary-light">
            Index tools after this connector is configured and connected.
          </p>
        ) : null}
        {tab === "connect" ? (
          <ConnectPane
            method={connectMethod}
            connected={phase === "after-setup"}
          />
        ) : null}
        {tab === "configure" ? (
          <ConfigForm
            registered={phase === "after-setup"}
            onRegister={onRegister}
          />
        ) : null}
      </div>
    </PreviewModal>
  );
}

function WizardModal({
  phase,
  integration,
  tools,
  connectMethod,
  onClose,
  onRegister,
  onUpdateToolAccess,
}: {
  phase: PhaseId;
  integration: HubIntegration;
  tools: HubTool[];
  connectMethod: ConnectMethod;
  onClose: () => void;
  onRegister: () => void;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  const [step, setStep] = useState<SetupPane>("configure");

  if (phase === "after-setup") {
    return (
      <ToolsFirstModal
        phase="after-setup"
        integration={integration}
        tools={tools}
        connectMethod={connectMethod}
        onClose={onClose}
        onRegister={onRegister}
        onUpdateToolAccess={onUpdateToolAccess}
      />
    );
  }

  const steps: SetupPane[] = ["configure", "connect", "tools"];
  const index = steps.indexOf(step);

  return (
    <PreviewModal
      testId="connector-variant-wizard"
      onClose={onClose}
      showUpdated={false}
      footer={
        <>
          <BrandButton
            type="button"
            variant="secondary"
            isDisabled={index === 0}
            onClick={() => setStep(steps[Math.max(0, index - 1)])}
          >
            Back
          </BrandButton>
          <BrandButton
            type="button"
            variant="primary"
            onClick={() => {
              if (step === "configure") {
                onRegister();
              }
              if (index < steps.length - 1) {
                setStep(steps[index + 1]);
              }
            }}
          >
            {WIZARD_PRIMARY_LABEL[step]}
          </BrandButton>
        </>
      }
    >
      <ConnectorHeader integration={integration} />
      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
        <ol className="mb-4 flex flex-wrap items-center gap-2 text-xs">
          {steps.map((item, stepIndex) => {
            const current = item === step;
            const done = stepIndex < index;
            return (
              <li key={item} className="flex items-center gap-2">
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5",
                    current && "font-medium text-white",
                    done && "text-[var(--oh-color-success)]",
                    !current && !done && "text-muted",
                  )}
                >
                  {done ? (
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  ) : (
                    <CircleDashed className="h-3.5 w-3.5" />
                  )}
                  {stepIndex + 1}. {SETUP_PANE_LABEL[item]}
                </span>
                {stepIndex < steps.length - 1 ? (
                  <ChevronRight className="h-3 w-3 text-muted" />
                ) : null}
              </li>
            );
          })}
        </ol>
        {step === "configure" ? (
          <ConfigForm registered={false} onRegister={onRegister} />
        ) : null}
        {step === "connect" ? (
          <ConnectPane method={connectMethod} connected={false} />
        ) : null}
        {step === "tools" ? (
          <p className="text-sm leading-6 text-tertiary-light">
            After you finish, reopen this connector and you will land on the
            tools list — not this wizard.
          </p>
        ) : null}
      </div>
    </PreviewModal>
  );
}

function CustomModal({
  phase,
  integration,
  tools,
  connectMethod,
  onUpdateToolAccess,
}: {
  phase: PhaseId;
  integration: HubIntegration;
  tools: HubTool[];
  connectMethod: ConnectMethod;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  return (
    <ToolsFirstModal
      phase={phase}
      integration={integration}
      tools={tools}
      connectMethod={connectMethod}
      onClose={() => undefined}
      onRegister={() => undefined}
      onUpdateToolAccess={onUpdateToolAccess}
      hideConfiguredBadge
    />
  );
}

function VariantPreview({
  variant,
  phase,
  integration,
  tools,
  connectMethod,
  onUpdateToolAccess,
}: {
  variant: VariantId;
  phase: PhaseId;
  integration: HubIntegration;
  tools: HubTool[];
  connectMethod: ConnectMethod;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  if (variant === "custom") {
    return (
      <CustomModal
        phase={phase}
        integration={integration}
        tools={tools}
        connectMethod={connectMethod}
        onUpdateToolAccess={onUpdateToolAccess}
      />
    );
  }

  if (variant === "current") {
    return (
      <PreviewModal
        testId="connector-variant-current"
        onClose={() => undefined}
        showUpdated={phase === "after-setup"}
        footer={
          <BrandButton type="button" variant="secondary">
            Close
          </BrandButton>
        }
      >
        <ConnectorHeader integration={integration} />
        <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
          <ConnectorSetupProgress
            integration={integration}
            isRegistered={phase === "after-setup"}
            onRegister={() => undefined}
            onDelete={() => undefined}
            onToolAccessModeChange={onUpdateToolAccess}
          />
        </div>
      </PreviewModal>
    );
  }

  if (variant === "tabs") {
    return (
      <TabsModal
        phase={phase}
        integration={integration}
        tools={tools}
        connectMethod={connectMethod}
        onClose={() => undefined}
        onRegister={() => undefined}
        onUpdateToolAccess={onUpdateToolAccess}
      />
    );
  }

  if (variant === "wizard") {
    return (
      <WizardModal
        phase={phase}
        integration={integration}
        tools={tools}
        connectMethod={connectMethod}
        onClose={() => undefined}
        onRegister={() => undefined}
        onUpdateToolAccess={onUpdateToolAccess}
      />
    );
  }

  return (
    <ToolsFirstModal
      phase={phase}
      integration={integration}
      tools={tools}
      connectMethod={connectMethod}
      onClose={() => undefined}
      onRegister={() => undefined}
      onUpdateToolAccess={onUpdateToolAccess}
    />
  );
}

export function ConnectorModalVariantsPage() {
  const [variant, setVariant] = useState<VariantId>("custom");
  const [phase, setPhase] = useState<PhaseId>("after-setup");
  const [connectMethod, setConnectMethod] = useState<ConnectMethod>("oauth");
  const [tools, setTools] = useState<HubTool[]>(INDEXED_TOOLS);
  const integration = useMemo(() => createGithub(phase, tools), [phase, tools]);
  const note = VARIANT_NOTES[variant];

  const updateToolAccess = (toolName: string, mode: HubToolAccessMode) => {
    setTools((current) =>
      current.map((tool) =>
        tool.name === toolName ? { ...tool, accessMode: mode } : tool,
      ),
    );
  };

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="connector-modal-variants-page"
    >
      <header className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-medium uppercase tracking-[0.04em] text-muted">
            Design review
          </p>
          <h1 className="text-xl font-medium leading-6 tracking-[-0.02em] text-white">
            Connector modal variants
          </h1>
          <p className="text-sm leading-5 text-tertiary-light">
            First-time configuration versus reopening GitHub after tools are
            indexed. These previews use the live Hub tokens and components.
          </p>
        </div>
        <Link
          to={INTEGRATIONS_HUB_PATHS.adminCatalog}
          className="shrink-0 text-sm text-tertiary-light hover:text-white"
        >
          Back to catalog
        </Link>
      </header>

      <div className="rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] px-4 py-3 text-sm leading-6 text-tertiary-light">
        Wizards are for infrequent, dependent setup. After that, the same
        surface should behave like a manage page. Our user Integration detail
        modal already does this. Auth0, GitHub Apps, Slack, and Stripe Connect
        all split install from later settings. NN/G: do not keep a wizard as the
        permanent home for a frequent task.
      </div>

      <div className="flex flex-col gap-3">
        <HubFilterTabs
          value={variant}
          onChange={setVariant}
          ariaLabel="Modal variants"
          testId="connector-modal-variant"
          options={VARIANTS.map((item) => ({
            value: item.value,
            label: item.recommended
              ? `${item.label} · recommended`
              : item.label,
          }))}
        />
        <HubFilterTabs
          value={phase}
          onChange={setPhase}
          ariaLabel="Setup phase"
          testId="connector-modal-phase"
          options={[
            { value: "first-run", label: "First configuration" },
            { value: "after-setup", label: "After setup · index tools" },
          ]}
        />
      </div>

      <div className="space-y-1">
        <p className="text-sm text-white">{note.why}</p>
        <p className="text-sm text-tertiary-light">
          {phase === "first-run" ? note.firstRun : note.afterSetup}
        </p>
      </div>

      <div
        className="flex justify-center rounded-2xl border border-[var(--oh-border)] bg-[var(--oh-color-base)] px-4 py-8"
        data-testid="connector-modal-variant-stage"
      >
        <VariantPreview
          key={`${variant}-${phase}`}
          variant={variant}
          phase={phase}
          integration={integration}
          tools={tools}
          connectMethod={connectMethod}
          onUpdateToolAccess={updateToolAccess}
        />
      </div>

      <div
        className="space-y-2 rounded-xl border border-dashed border-[var(--oh-border)] px-4 py-3"
        data-testid="connector-modal-connect-method-stage"
      >
        <p className="text-xs font-medium uppercase tracking-[0.04em] text-muted">
          Testing only · Connection method
        </p>
        <p className="text-xs leading-5 text-tertiary-light">
          Admins do not choose this in the modal. The connector&apos;s auth
          strategy decides it.
        </p>
        <HubFilterTabs
          value={connectMethod}
          onChange={setConnectMethod}
          ariaLabel="Testing connection methods"
          testId="connector-modal-connect-method"
          options={CONNECT_METHODS}
        />
      </div>
    </div>
  );
}
