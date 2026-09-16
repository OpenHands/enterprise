import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
  type Ref,
} from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clock,
  RefreshCw,
  Settings2,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { HubIntegrationModalHeader } from "#/components/features/integrations-hub/hub-integration-modal-header";
import { HubIntegrationEnableRow } from "#/components/features/integrations-hub/integration-detail-modal";
import { HubToolAccessList } from "#/components/features/integrations-hub/hub-tool-access-list";
import {
  createConfigForm,
  getConnectorAuthRequirements,
  resolveIndexedTools,
  type ConnectorConfigForm,
  type ConnectorConnectMethod,
} from "#/components/features/integrations-hub/connector-requirements";
import {
  HubModal,
  hubModalBodyClassName,
  hubModalFooterClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { StyledTooltip } from "#/components/shared/buttons/styled-tooltip";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubConnectorProvider,
  HubIntegration,
  HubTool,
  HubToolAccessMode,
} from "#/types/integrations-hub";
import {
  formControlFieldClassName,
  formControlNativeSelectClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

type SetupStep = "configure" | "connect" | "tools";
type PhaseId = "first-run" | "after-setup";

const INDEX_TOOLS_DELAY_MS = 800;

const INVALID_FIELD_CLASS_NAME =
  "border-[var(--oh-color-danger)] bg-[color:rgba(231,106,94,0.08)] focus:border-[var(--oh-color-danger)] focus:ring-[color:rgba(231,106,94,0.35)]";

interface CatalogConnectorContentProps {
  integration: HubIntegration;
  phase?: PhaseId;
  availableTools?: HubTool[];
  onClose: () => void;
  onRegister: () => void;
  onDelete: () => void;
  onToggleEnabled: () => void;
  onToolAccessModeChange: (toolName: string, mode: HubToolAccessMode) => void;
}

function Field({
  label,
  invalid,
  children,
}: {
  label: string;
  invalid?: boolean;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "flex flex-col gap-1 text-xs",
        invalid
          ? "text-[var(--oh-color-danger)]"
          : "text-[var(--oh-text-secondary)]",
      )}
    >
      {label}
      {children}
    </label>
  );
}

function SetupCard({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] p-4">
      <h3 className="text-sm font-semibold text-white">{title}</h3>
      <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
        {description}
      </p>
      <div className="mt-4">{children}</div>
    </div>
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
  integration,
  registered,
  confirmBeforeContinue = false,
  hidePrimaryAction = false,
  formRef,
  onRegister,
  onConfirmedChange,
}: {
  integration: HubIntegration;
  registered: boolean;
  confirmBeforeContinue?: boolean;
  hidePrimaryAction?: boolean;
  formRef?: Ref<HTMLFormElement>;
  onRegister: () => void;
  onConfirmedChange?: (confirmed: boolean) => void;
}) {
  const { t } = useTranslation();
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [form, setForm] = useState(() => createConfigForm(integration));
  const [status, setStatus] = useState<"idle" | "success" | "error">("idle");
  const requirements = getConnectorAuthRequirements(integration, form.provider);
  const callbackUrl = `${window.location.origin}/api/oauth/${integration.slug}/callback`;
  const clientIdInvalid =
    status === "error" &&
    requirements.needsManualOAuthClient &&
    !form.oauthClientId.trim();
  const clientSecretInvalid =
    status === "error" &&
    requirements.needsManualOAuthClient &&
    !form.oauthClientSecret.trim();

  const updateForm = (updates: Partial<ConnectorConfigForm>) => {
    setForm((current) => ({ ...current, ...updates }));
    if (confirmBeforeContinue && status !== "idle") {
      setStatus("idle");
      onConfirmedChange?.(false);
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      confirmBeforeContinue &&
      requirements.needsManualOAuthClient &&
      (!form.oauthClientId.trim() || !form.oauthClientSecret.trim())
    ) {
      setStatus("error");
      onConfirmedChange?.(false);
      return;
    }
    setStatus("success");
    onConfirmedChange?.(true);
    onRegister();
  };

  return (
    <form ref={formRef} className="grid gap-3" onSubmit={handleSubmit}>
      <Field label={t(I18nKey.INTEGRATIONS_HUB$SETUP_PROVIDER_TYPE)}>
        <select
          value={form.provider}
          onChange={(event) =>
            updateForm({
              provider: event.target.value as HubConnectorProvider,
            })
          }
          className={formControlNativeSelectClassName}
        >
          <option value="mcp">
            {t(I18nKey.INTEGRATIONS_HUB$SETUP_PROVIDER_MCP)}
          </option>
          <option value="http">
            {t(I18nKey.INTEGRATIONS_HUB$SETUP_PROVIDER_HTTP)}
          </option>
        </select>
      </Field>
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
          {t(I18nKey.INTEGRATIONS_HUB$SETUP_ADVANCED)}
        </button>
        {advancedOpen ? (
          <div className="mt-3 grid gap-3">
            {form.provider === "http" ? (
              <>
                <Field label={t(I18nKey.INTEGRATIONS_HUB$SETUP_API_BASE_URL)}>
                  <input
                    type="url"
                    value={form.apiBaseUrl}
                    onChange={(event) =>
                      updateForm({ apiBaseUrl: event.target.value })
                    }
                    className={formControlFieldClassName}
                  />
                </Field>
                <Field label={t(I18nKey.INTEGRATIONS_HUB$SETUP_OPENAPI_URL)}>
                  <input
                    type="url"
                    value={form.openApiUrl}
                    placeholder="https://developers.example.com/openapi.json"
                    onChange={(event) =>
                      updateForm({ openApiUrl: event.target.value })
                    }
                    className={formControlFieldClassName}
                  />
                </Field>
              </>
            ) : (
              <Field label={t(I18nKey.INTEGRATIONS_HUB$SETUP_MCP_SERVER_URL)}>
                <input
                  type="url"
                  value={form.serverUrl}
                  onChange={(event) =>
                    updateForm({ serverUrl: event.target.value })
                  }
                  className={formControlFieldClassName}
                />
              </Field>
            )}
            {requirements.isOAuth ? (
              <>
                <Field label={t(I18nKey.INTEGRATIONS_HUB$SETUP_AUTH_URL)}>
                  <input
                    type="url"
                    value={form.authorizationUrl}
                    onChange={(event) =>
                      updateForm({ authorizationUrl: event.target.value })
                    }
                    className={formControlFieldClassName}
                  />
                </Field>
                <Field label={t(I18nKey.INTEGRATIONS_HUB$SETUP_TOKEN_URL)}>
                  <input
                    type="url"
                    value={form.tokenUrl}
                    onChange={(event) =>
                      updateForm({ tokenUrl: event.target.value })
                    }
                    className={formControlFieldClassName}
                  />
                </Field>
                <Field
                  label={t(I18nKey.INTEGRATIONS_HUB$SETUP_REQUIRED_SCOPES)}
                >
                  <input
                    type="text"
                    value={form.scopes}
                    onChange={(event) =>
                      updateForm({ scopes: event.target.value })
                    }
                    className={formControlFieldClassName}
                  />
                </Field>
                <Field
                  label={t(I18nKey.INTEGRATIONS_HUB$SETUP_OPTIONAL_SCOPES)}
                >
                  <input
                    type="text"
                    value={form.optionalScopes}
                    placeholder="contacts.write, settings.read"
                    onChange={(event) =>
                      updateForm({ optionalScopes: event.target.value })
                    }
                    className={formControlFieldClassName}
                  />
                </Field>
              </>
            ) : null}
          </div>
        ) : null}
      </div>
      {requirements.needsManualOAuthClient ? (
        <>
          <Field
            label={t(
              registered
                ? I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_ID_REPLACE
                : I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_ID,
            )}
            invalid={clientIdInvalid}
          >
            <input
              className={cn(
                formControlFieldClassName,
                clientIdInvalid && INVALID_FIELD_CLASS_NAME,
              )}
              type="text"
              value={form.oauthClientId}
              onChange={(event) =>
                updateForm({ oauthClientId: event.target.value })
              }
              aria-invalid={clientIdInvalid}
              aria-describedby={
                status === "error"
                  ? "catalog-connector-register-error"
                  : undefined
              }
              data-testid="tools-first-client-id"
            />
          </Field>
          <Field
            label={t(
              registered
                ? I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_SECRET_REPLACE
                : I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_SECRET,
            )}
            invalid={clientSecretInvalid}
          >
            <input
              className={cn(
                formControlFieldClassName,
                clientSecretInvalid && INVALID_FIELD_CLASS_NAME,
              )}
              type="password"
              value={form.oauthClientSecret}
              onChange={(event) =>
                updateForm({ oauthClientSecret: event.target.value })
              }
              aria-invalid={clientSecretInvalid}
              aria-describedby={
                status === "error"
                  ? "catalog-connector-register-error"
                  : undefined
              }
              data-testid="tools-first-client-secret"
            />
          </Field>
          <div className="rounded-lg border border-[var(--oh-border)] bg-[var(--oh-bg-input)] px-3 py-2 text-[11px] leading-5 text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$SETUP_REDIRECT_HINT, {
              url: callbackUrl,
            })}
          </div>
        </>
      ) : null}
      {hidePrimaryAction ? null : (
        <div className="flex flex-wrap items-center gap-3">
          <BrandButton
            type="submit"
            variant="primary"
            testId={
              registered
                ? `admin-catalog-save-${integration.slug}`
                : `admin-catalog-register-${integration.slug}`
            }
          >
            {registered
              ? t(I18nKey.INTEGRATIONS_HUB$SETUP_SAVE_CONFIG)
              : t(I18nKey.INTEGRATIONS_HUB$REGISTER)}
          </BrandButton>
        </div>
      )}
      {confirmBeforeContinue && status === "error" ? (
        <p
          id="catalog-connector-register-error"
          className="flex w-full items-start gap-1.5 rounded-lg border border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.1)] py-2 pl-4 pr-3 text-xs leading-5 text-[var(--oh-color-danger)]"
          data-testid="tools-first-register-error"
          role="alert"
        >
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t(I18nKey.INTEGRATIONS_HUB$SETUP_REGISTER_ERROR)}
        </p>
      ) : null}
    </form>
  );
}

function ConnectPane({
  method,
  connected,
  confirmBeforeContinue = false,
  hidePrimaryAction = false,
  formRef,
  onConnect,
  onConfirmedChange,
}: {
  method: ConnectorConnectMethod;
  connected: boolean;
  confirmBeforeContinue?: boolean;
  hidePrimaryAction?: boolean;
  formRef?: Ref<HTMLFormElement>;
  onConnect?: () => void;
  onConfirmedChange?: (confirmed: boolean) => void;
}) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState("");
  const [status, setStatus] = useState<"idle" | "success" | "error">(
    connected ? "success" : "idle",
  );
  const apiKeyInvalid =
    status === "error" && method === "api_key" && !apiKey.trim();
  const canConnect = method === "oauth" || Boolean(apiKey.trim());

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!canConnect) {
      setStatus("error");
      onConfirmedChange?.(false);
      return;
    }
    setStatus("success");
    onConfirmedChange?.(true);
    onConnect?.();
  };

  return (
    <form ref={formRef} className="space-y-3" onSubmit={handleSubmit}>
      {method === "api_key" ? (
        <Field
          label={t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_API_KEY)}
          invalid={apiKeyInvalid}
        >
          <input
            className={cn(
              formControlFieldClassName,
              apiKeyInvalid && INVALID_FIELD_CLASS_NAME,
            )}
            type="password"
            value={apiKey}
            onChange={(event) => {
              setApiKey(event.target.value);
              if (status !== "idle") {
                setStatus("idle");
                onConfirmedChange?.(false);
              }
            }}
            aria-invalid={apiKeyInvalid}
            aria-describedby={
              status === "error" ? "catalog-connector-connect-error" : undefined
            }
            data-testid="tools-first-connect-api-key"
          />
        </Field>
      ) : null}
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
          {connected
            ? t(I18nKey.INTEGRATIONS_HUB$SETUP_RECONNECT)
            : t(
                status === "success"
                  ? I18nKey.INTEGRATIONS_HUB$SETUP_CONNECTED
                  : I18nKey.INTEGRATIONS_HUB$CONNECT,
              )}
        </BrandButton>
      )}
      {confirmBeforeContinue && status === "error" ? (
        <p
          id="catalog-connector-connect-error"
          className="flex w-full items-start gap-1.5 rounded-lg border border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.1)] py-2 pl-4 pr-3 text-xs leading-5 text-[var(--oh-color-danger)]"
          data-testid="tools-first-connect-error"
          role="alert"
        >
          <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {method === "api_key"
            ? t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_API_ERROR)
            : t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_OAUTH_ERROR)}
        </p>
      ) : null}
    </form>
  );
}

function ToolsPane({
  tools,
  searchTestId,
  isIndexing,
  onIndex,
  onUpdateToolAccess,
}: {
  tools: HubTool[];
  searchTestId: string;
  isIndexing: boolean;
  onIndex: () => void;
  onUpdateToolAccess: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  const { t } = useTranslation();
  const indexButton = (
    <StyledTooltip
      content={t(I18nKey.INTEGRATIONS_HUB$SETUP_REFRESH_LIST)}
      placement="top"
    >
      <BrandButton
        type="button"
        variant="secondary"
        testId="tools-first-index-tools"
        ariaLabel={t(I18nKey.INTEGRATIONS_HUB$SETUP_REFRESH_LIST)}
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
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEXING)}
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
      searchTestId={searchTestId}
      toolbarEnd={indexButton}
      onUpdateToolAccess={onUpdateToolAccess}
    />
  );
}

function CatalogConnectorContent({
  integration,
  phase: phaseOverride,
  availableTools,
  onClose,
  onRegister,
  onDelete,
  onToggleEnabled,
  onToolAccessModeChange,
}: CatalogConnectorContentProps) {
  const { t } = useTranslation();
  const [phase] = useState<PhaseId>(
    () =>
      phaseOverride ?? (integration.connected ? "after-setup" : "first-run"),
  );
  const [view, setView] = useState<"work" | "settings">("work");
  const [setupStep, setSetupStep] = useState<SetupStep>("configure");
  const [indexedTools, setIndexedTools] = useState<HubTool[]>(() =>
    phase === "after-setup"
      ? resolveIndexedTools(integration, availableTools)
      : [],
  );
  const [isIndexing, setIsIndexing] = useState(false);
  const [connectReady, setConnectReady] = useState(false);
  const configFormRef = useRef<HTMLFormElement>(null);
  const connectFormRef = useRef<HTMLFormElement>(null);
  const indexTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requirements = getConnectorAuthRequirements(integration);
  const toolsToIndex = resolveIndexedTools(integration, availableTools);

  let configureDescriptionKey =
    I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_API_DESC;
  if (requirements.isDynamicOAuth || requirements.usesDynamicRegistration) {
    configureDescriptionKey =
      I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_DYNAMIC_DESC;
  } else if (requirements.isOAuth) {
    configureDescriptionKey =
      I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_OAUTH_DESC;
  }
  const configureDescription = t(configureDescriptionKey);
  const connectDescription =
    requirements.connectMethod === "api_key"
      ? t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_API_HELP)
      : t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_OAUTH_HELP);

  const runIndex = () => {
    if (indexTimeoutRef.current) {
      clearTimeout(indexTimeoutRef.current);
    }
    setIsIndexing(true);
    setIndexedTools([]);
    indexTimeoutRef.current = setTimeout(() => {
      setIndexedTools(toolsToIndex);
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

  const goToTools = () => {
    setSetupStep("tools");
    runIndex();
  };

  const goNext = () => {
    if (setupStep === "configure") {
      configFormRef.current?.requestSubmit();
      return;
    }
    if (setupStep === "connect" && connectReady) {
      goToTools();
    }
  };

  const handleConfigureSuccess = () => {
    onRegister();
    if (requirements.needsConnect) {
      setSetupStep("connect");
      return;
    }
    goToTools();
  };

  if (phase === "first-run") {
    return (
      <>
        <ConnectorHeader integration={integration} />
        <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
          {setupStep === "configure" ? (
            <SetupCard
              title={t(I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_TITLE)}
              description={configureDescription}
            >
              <ConfigForm
                integration={integration}
                registered={false}
                confirmBeforeContinue
                hidePrimaryAction
                formRef={configFormRef}
                onRegister={handleConfigureSuccess}
              />
            </SetupCard>
          ) : null}
          {setupStep === "connect" ? (
            <SetupCard
              title={t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_TITLE)}
              description={connectDescription}
            >
              <ConnectPane
                method={requirements.connectMethod}
                connected={false}
                confirmBeforeContinue
                formRef={connectFormRef}
                onConfirmedChange={setConnectReady}
              />
            </SetupCard>
          ) : null}
          {setupStep === "tools" ? (
            <ToolsPane
              tools={indexedTools}
              searchTestId={`connector-tools-search-${integration.slug}`}
              isIndexing={isIndexing}
              onIndex={runIndex}
              onUpdateToolAccess={onToolAccessModeChange}
            />
          ) : null}
        </div>
        <div className={cn(hubModalFooterClassName, "justify-end")}>
          <div className="flex flex-wrap items-center gap-2">
            {setupStep === "configure" ? (
              <BrandButton
                type="button"
                variant="secondary"
                testId="tools-first-cancel"
                onClick={onClose}
              >
                {t(I18nKey.BUTTON$CANCEL)}
              </BrandButton>
            ) : (
              <BrandButton
                type="button"
                variant="secondary"
                onClick={() =>
                  setSetupStep(
                    setupStep === "tools" && requirements.needsConnect
                      ? "connect"
                      : "configure",
                  )
                }
              >
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_BACK)}
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
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_DONE)}
              </BrandButton>
            ) : (
              <BrandButton
                type="button"
                variant="primary"
                testId="tools-first-next"
                isDisabled={setupStep === "connect" && !connectReady}
                onClick={goNext}
              >
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_NEXT)}
              </BrandButton>
            )}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <ConnectorHeader integration={integration}>
        <HubIntegrationEnableRow
          integration={integration}
          onToggleEnabled={onToggleEnabled}
        />
      </ConnectorHeader>
      <div className="min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-4">
        {view === "work" ? (
          <ToolsPane
            tools={indexedTools}
            searchTestId={`connector-tools-search-${integration.slug}`}
            isIndexing={isIndexing}
            onIndex={runIndex}
            onUpdateToolAccess={onToolAccessModeChange}
          />
        ) : (
          <div className="space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-white">
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECTOR_SETTINGS)}
              </h3>
              <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_SETTINGS_DESC)}
              </p>
            </div>
            <ConfigForm
              integration={integration}
              registered
              onRegister={() => undefined}
            />
            {requirements.needsConnect ? (
              <div className="rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)] p-4">
                <h4 className="text-sm font-semibold text-white">
                  {t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECTED_ACCOUNT)}
                </h4>
                <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
                  {t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECTED_AS, {
                    name: "you",
                  })}
                </p>
                <div className="mt-3">
                  <ConnectPane method={requirements.connectMethod} connected />
                </div>
              </div>
            ) : null}
            <div className="flex items-center justify-between gap-4 rounded-xl border border-[color:rgba(231,106,94,0.3)] bg-[color:rgba(231,106,94,0.08)] px-4 py-3">
              <p className="min-w-0 text-sm leading-5 text-white">
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_DELETE_BAR)}
              </p>
              <BrandButton
                type="button"
                variant="danger"
                className="shrink-0"
                testId={`admin-catalog-delete-${integration.slug}`}
                ariaLabel={t(I18nKey.INTEGRATIONS_HUB$SETUP_DELETE_ARIA, {
                  name: integration.name,
                })}
                startContent={<Trash2 className="h-4 w-4" />}
                onClick={onDelete}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DELETE)}
              </BrandButton>
            </div>
          </div>
        )}
      </div>
      <div className={cn(hubModalFooterClassName, "justify-between")}>
        <p className="inline-flex items-center gap-1.5 text-[11px] leading-5 text-[var(--oh-text-secondary)]">
          <Clock className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {t(I18nKey.INTEGRATIONS_HUB$SETUP_UPDATED, {
            when: integration.updatedAt
              ? new Date(integration.updatedAt).toLocaleString()
              : new Date().toLocaleString(),
          })}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {view === "work" ? (
            <BrandButton
              type="button"
              variant="secondary"
              startContent={<Settings2 className="h-4 w-4" />}
              onClick={() => setView("settings")}
            >
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECTOR_SETTINGS)}
            </BrandButton>
          ) : (
            <BrandButton
              type="button"
              variant="secondary"
              onClick={() => setView("work")}
            >
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_BACK_TO_TOOLS)}
            </BrandButton>
          )}
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.INTEGRATIONS_HUB$CLOSE)}
          </BrandButton>
        </div>
      </div>
    </>
  );
}

export function CatalogConnectorModal({
  integration,
  onClose,
  onRegister,
  onDelete,
  onToggleEnabled,
  onToolAccessModeChange,
}: Omit<CatalogConnectorContentProps, "phase" | "availableTools">) {
  const { t } = useTranslation();
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <>
      <HubModal
        ariaLabel={t(I18nKey.INTEGRATIONS_HUB$CONNECTOR_DETAILS, {
          name: integration.name,
        })}
        testId={`connector-details-modal-${integration.slug}`}
        width="xl"
        className="min-h-0 max-h-[90vh] gap-0"
        onClose={onClose}
      >
        <CatalogConnectorContent
          integration={integration}
          onClose={onClose}
          onRegister={onRegister}
          onDelete={() => setConfirmDelete(true)}
          onToggleEnabled={onToggleEnabled}
          onToolAccessModeChange={onToolAccessModeChange}
        />
      </HubModal>
      {confirmDelete ? (
        <HubModal
          ariaLabel={t(I18nKey.INTEGRATIONS_HUB$SETUP_DELETE_CONFIRM_TITLE)}
          testId="delete-connector-modal"
          width="md"
          onClose={() => setConfirmDelete(false)}
        >
          <div className={hubModalBodyClassName}>
            <h2 className="pr-8 text-base font-semibold text-white">
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_DELETE_CONFIRM_TITLE)}
            </h2>
            <p className="mt-2 text-sm text-tertiary-light">
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_DELETE_CONFIRM_BODY, {
                name: integration.name,
              })}
            </p>
          </div>
          <div className={hubModalFooterClassName}>
            <BrandButton
              type="button"
              variant="secondary"
              testId="delete-connector-cancel"
              onClick={() => setConfirmDelete(false)}
            >
              {t(I18nKey.BUTTON$CANCEL)}
            </BrandButton>
            <BrandButton
              type="button"
              variant="danger"
              testId="delete-connector-confirm"
              onClick={onDelete}
            >
              {t(I18nKey.INTEGRATIONS_HUB$DELETE)}
            </BrandButton>
          </div>
        </HubModal>
      ) : null}
    </>
  );
}
