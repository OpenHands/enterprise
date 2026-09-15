import {
  type FormEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { HubBadge } from "#/components/features/integrations-hub/hub-badge";
import { HubToolAccessList } from "#/components/features/integrations-hub/hub-tool-access-list";
import { BrandButton } from "#/components/features/settings/brand-button";
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

const STUB_INDEXED_TOOL: HubTool = {
  name: "example_tool",
  description: "Indexed from the connector schema in this preview.",
  accessMode: "enabled",
  defaultScopes: [],
};

export interface ConnectorConfigForm {
  provider: HubConnectorProvider;
  apiBaseUrl: string;
  serverUrl: string;
  openApiUrl: string;
  authorizationUrl: string;
  tokenUrl: string;
  scopes: string;
  optionalScopes: string;
  oauthClientId: string;
  oauthClientSecret: string;
}

function createConfigForm(integration: HubIntegration): ConnectorConfigForm {
  return {
    provider:
      integration.connectorProvider ??
      (integration.openApiUrl ? "http" : "mcp"),
    apiBaseUrl: integration.apiBaseUrl ?? "",
    serverUrl: integration.serverUrl ?? "",
    openApiUrl: integration.openApiUrl ?? "",
    authorizationUrl: integration.oauthConfig?.authorizationUrl ?? "",
    tokenUrl: integration.oauthConfig?.tokenUrl ?? "",
    scopes: (integration.oauthConfig?.scopes ?? []).join(", "),
    optionalScopes: (integration.oauthConfig?.optionalScopes ?? []).join(", "),
    oauthClientId: "",
    oauthClientSecret: "",
  };
}

function isOAuthMcp(
  integration: HubIntegration,
  provider: HubConnectorProvider,
) {
  return (
    integration.authStrategy === "oauth2" &&
    provider === "mcp" &&
    !integration.openApiUrl
  );
}

function StepCard({
  stepNumber,
  title,
  description,
  completed,
  disabled,
  disabledHint,
  children,
  stepKey,
}: {
  stepNumber: number;
  title: string;
  description: string;
  completed: boolean;
  disabled?: boolean;
  disabledHint?: string;
  children?: ReactNode;
  stepKey: string;
}) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const wasCompletedRef = useRef(completed);

  useEffect(() => {
    if (completed && !wasCompletedRef.current) {
      setCollapsed(true);
    }
    wasCompletedRef.current = completed;
  }, [completed]);

  return (
    <div
      className={cn(
        "rounded-xl border bg-[var(--oh-surface-subtle)] p-4",
        completed
          ? "border-[color:rgba(165,231,94,0.3)]"
          : "border-[var(--oh-border)]",
      )}
      data-setup-step={stepKey}
      data-setup-step-completed={completed ? "true" : "false"}
    >
      <div className="flex items-start gap-2">
        {completed ? (
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-[var(--oh-color-success)]" />
        ) : (
          <CircleDashed
            className={cn(
              "mt-0.5 h-5 w-5 shrink-0 text-muted",
              disabled && "opacity-50",
            )}
          />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted">
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_STEP, { number: stepNumber })}
            </span>
            {completed ? (
              <HubBadge size="sm" tone="success">
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_DONE)}
              </HubBadge>
            ) : null}
            {completed && children ? (
              <button
                type="button"
                onClick={() => setCollapsed(!collapsed)}
                className="ml-auto inline-flex items-center gap-1 text-xs text-muted hover:text-[var(--oh-text-secondary)]"
                aria-expanded={!collapsed}
                aria-label={
                  collapsed
                    ? t(I18nKey.INTEGRATIONS_HUB$SETUP_EXPAND, { title })
                    : t(I18nKey.INTEGRATIONS_HUB$SETUP_COLLAPSE, { title })
                }
              >
                {collapsed ? (
                  <ChevronRight className="h-3.5 w-3.5" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5" />
                )}
                {collapsed
                  ? t(I18nKey.INTEGRATIONS_HUB$SETUP_SHOW)
                  : t(I18nKey.INTEGRATIONS_HUB$SETUP_HIDE)}
              </button>
            ) : null}
          </div>
          <h4 className="mt-0.5 text-sm font-semibold text-white">{title}</h4>
          <p className="mt-1 text-xs leading-5 text-[var(--oh-text-secondary)]">
            {description}
          </p>
          {disabledHint && !completed ? (
            <p className="mt-1 text-xs leading-5 text-muted">{disabledHint}</p>
          ) : null}
        </div>
      </div>
      {children && !collapsed ? (
        <div className="mt-3 flex items-start gap-2">
          <span className="h-5 w-5 shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">{children}</div>
        </div>
      ) : null}
    </div>
  );
}

function Field({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <label
      className={cn(
        "flex flex-col gap-1 text-xs text-[var(--oh-text-secondary)]",
        className,
      )}
    >
      {label}
      {children}
    </label>
  );
}

export function ConnectorSetupProgress({
  integration,
  isRegistered,
  onRegister,
  onDelete,
  onToolAccessModeChange,
}: {
  integration: HubIntegration;
  isRegistered: boolean;
  onRegister: () => void;
  onDelete: () => void;
  onToolAccessModeChange: (toolName: string, mode: HubToolAccessMode) => void;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState(() => createConfigForm(integration));
  const [accountConnected, setAccountConnected] = useState(
    () =>
      isRegistered &&
      isOAuthMcp(integration, createConfigForm(integration).provider),
  );
  const [localTools, setLocalTools] = useState<HubTool[]>([]);
  const [message, setMessage] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const oauthMcp = isOAuthMcp(integration, form.provider);
  const isOAuth = integration.authStrategy === "oauth2";
  const isDynamicOAuth = isOAuth && !integration.oauthConfig?.authorizationUrl;
  const usesDynamicRegistration =
    isOAuth &&
    integration.oauthConfig?.clientAuthentication === "none" &&
    Boolean(integration.oauthConfig?.registrationUrl);
  const needsManualOAuthClient =
    isOAuth && !usesDynamicRegistration && !isDynamicOAuth;
  const isHttp = form.provider === "http";
  const tools = integration.tools.length > 0 ? integration.tools : localTools;
  const toolsIndexed = tools.length > 0;
  const clientConfigured =
    isRegistered && (isDynamicOAuth || needsManualOAuthClient);
  const step1Completed =
    isRegistered &&
    (oauthMcp ? clientConfigured || accountConnected || toolsIndexed : true);
  const callbackUrl = `${window.location.origin}/api/oauth/${integration.slug}/callback`;
  const updatedAt = integration.updatedAt
    ? new Date(integration.updatedAt).toLocaleString()
    : new Date().toLocaleString();

  const updateForm = (updates: Partial<ConnectorConfigForm>) =>
    setForm((current) => ({ ...current, ...updates }));

  const handleSave = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isRegistered) {
      onRegister();
      setMessage(t(I18nKey.INTEGRATIONS_HUB$SETUP_REGISTERED_MESSAGE));
      return;
    }
    setMessage(t(I18nKey.INTEGRATIONS_HUB$SETUP_SAVED_MESSAGE));
  };

  const handleIndex = () => {
    if (integration.tools.length === 0 && localTools.length === 0) {
      setLocalTools([STUB_INDEXED_TOOL]);
    }
    setMessage(
      t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEXED_MESSAGE, {
        count: Math.max(tools.length, 1),
      }),
    );
  };

  let configureDescription = t(
    I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_API_DESC,
  );
  if (isDynamicOAuth) {
    configureDescription = t(
      I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_DYNAMIC_DESC,
    );
  } else if (isOAuth) {
    configureDescription = t(
      I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_OAUTH_DESC,
    );
  }

  let indexDisabledHint: string | undefined;
  if (oauthMcp && !accountConnected) {
    indexDisabledHint = t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEX_OAUTH_HINT);
  } else if (!step1Completed) {
    indexDisabledHint = t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_HINT);
  }

  return (
    <div
      className="space-y-3"
      data-testid={`connector-setup-${integration.slug}`}
    >
      <StepCard
        stepNumber={1}
        title={t(I18nKey.INTEGRATIONS_HUB$SETUP_CONFIGURE_TITLE)}
        description={configureDescription}
        completed={step1Completed}
        stepKey="configure-integration"
      >
        <form className="grid gap-3 md:grid-cols-2" onSubmit={handleSave}>
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
          <div className="flex items-end text-[11px] leading-5 text-[var(--oh-text-secondary)]">
            {t(I18nKey.INTEGRATIONS_HUB$SETUP_UPDATED, { when: updatedAt })}
          </div>

          <div className="md:col-span-2">
            <button
              type="button"
              onClick={() => setAdvancedOpen(!advancedOpen)}
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
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {isHttp ? (
                  <>
                    <Field
                      className="md:col-span-2"
                      label={t(I18nKey.INTEGRATIONS_HUB$SETUP_API_BASE_URL)}
                    >
                      <input
                        type="url"
                        value={form.apiBaseUrl}
                        onChange={(event) =>
                          updateForm({ apiBaseUrl: event.target.value })
                        }
                        className={formControlFieldClassName}
                      />
                    </Field>
                    <Field
                      className="md:col-span-2"
                      label={t(I18nKey.INTEGRATIONS_HUB$SETUP_OPENAPI_URL)}
                    >
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
                  <Field
                    className="md:col-span-2"
                    label={t(I18nKey.INTEGRATIONS_HUB$SETUP_MCP_SERVER_URL)}
                  >
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
                {isOAuth ? (
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
                      className="md:col-span-2"
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
                      className="md:col-span-2"
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

          {needsManualOAuthClient ? (
            <>
              <Field
                label={t(
                  isRegistered
                    ? I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_ID_REPLACE
                    : I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_ID,
                )}
              >
                <input
                  type="text"
                  value={form.oauthClientId}
                  onChange={(event) =>
                    updateForm({ oauthClientId: event.target.value })
                  }
                  className={formControlFieldClassName}
                />
              </Field>
              <Field
                label={t(
                  isRegistered
                    ? I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_SECRET_REPLACE
                    : I18nKey.INTEGRATIONS_HUB$SETUP_CLIENT_SECRET,
                )}
              >
                <input
                  type="password"
                  value={form.oauthClientSecret}
                  onChange={(event) =>
                    updateForm({ oauthClientSecret: event.target.value })
                  }
                  className={formControlFieldClassName}
                />
              </Field>
              <div className="md:col-span-2 rounded-lg border border-[var(--oh-border)] bg-[var(--oh-bg-input)] px-3 py-2 text-[11px] leading-5 text-[var(--oh-text-secondary)]">
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_REDIRECT_HINT, {
                  url: callbackUrl,
                })}
              </div>
            </>
          ) : null}

          <div className="md:col-span-2 flex flex-wrap items-center gap-3">
            <BrandButton
              type="submit"
              variant="primary"
              testId={
                isRegistered
                  ? `admin-catalog-save-${integration.slug}`
                  : `admin-catalog-register-${integration.slug}`
              }
            >
              {isRegistered
                ? t(I18nKey.INTEGRATIONS_HUB$SETUP_SAVE_CONFIG)
                : t(I18nKey.INTEGRATIONS_HUB$REGISTER)}
            </BrandButton>
            {isRegistered ? (
              <BrandButton
                type="button"
                variant="danger"
                testId={`admin-catalog-delete-${integration.slug}`}
                ariaLabel={t(I18nKey.INTEGRATIONS_HUB$SETUP_DELETE_ARIA, {
                  name: integration.name,
                })}
                startContent={<Trash2 className="h-4 w-4" />}
                onClick={onDelete}
              >
                {t(I18nKey.INTEGRATIONS_HUB$DELETE)}
              </BrandButton>
            ) : null}
            {message ? (
              <p className="text-sm text-[var(--oh-text-secondary)]">
                {message}
              </p>
            ) : null}
          </div>
        </form>
      </StepCard>

      {oauthMcp ? (
        <StepCard
          stepNumber={2}
          title={t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_TITLE)}
          description={t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_DESC)}
          completed={accountConnected}
          disabled={!clientConfigured && !accountConnected}
          disabledHint={
            !clientConfigured && !accountConnected
              ? t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECT_HINT)
              : undefined
          }
          stepKey="connect-account"
        >
          <div className="flex flex-wrap items-center gap-3">
            {accountConnected ? (
              <span className="text-xs text-muted">
                {t(I18nKey.INTEGRATIONS_HUB$SETUP_CONNECTED_AS, {
                  name: "you",
                })}
              </span>
            ) : null}
            <BrandButton
              type="button"
              variant="primary"
              testId={`admin-catalog-oauth-${integration.slug}`}
              isDisabled={!clientConfigured && !accountConnected}
              onClick={() => setAccountConnected(true)}
            >
              {accountConnected
                ? t(I18nKey.INTEGRATIONS_HUB$SETUP_RECONNECT)
                : t(I18nKey.INTEGRATIONS_HUB$CONNECT)}
            </BrandButton>
          </div>
        </StepCard>
      ) : null}

      <StepCard
        stepNumber={oauthMcp ? 3 : 2}
        title={t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEX_TITLE)}
        description={
          oauthMcp
            ? t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEX_OAUTH_DESC)
            : t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEX_HTTP_DESC)
        }
        completed={toolsIndexed}
        disabled={oauthMcp ? !accountConnected : !step1Completed}
        disabledHint={indexDisabledHint}
        stepKey="index-tools"
      >
        <div className="flex flex-wrap items-center gap-3">
          <BrandButton
            type="button"
            variant="primary"
            testId={`admin-catalog-index-${integration.slug}`}
            isDisabled={oauthMcp ? !accountConnected : !step1Completed}
            startContent={<RefreshCw className="h-4 w-4" />}
            onClick={handleIndex}
          >
            {t(I18nKey.INTEGRATIONS_HUB$SETUP_INDEX_TOOLS)}
          </BrandButton>
          {toolsIndexed ? (
            <HubBadge size="sm" tone="success">
              {t(I18nKey.INTEGRATIONS_HUB$SETUP_TOOLS_INDEXED, {
                count: tools.length,
              })}
            </HubBadge>
          ) : null}
        </div>
        {toolsIndexed ? (
          <div className="mt-4">
            <HubToolAccessList
              tools={tools}
              searchTestId={`connector-tools-search-${integration.slug}`}
              onUpdateToolAccess={onToolAccessModeChange}
            />
          </div>
        ) : null}
      </StepCard>

      {step1Completed && (!oauthMcp || accountConnected) && toolsIndexed ? (
        <p className="rounded-lg border border-[color:rgba(165,231,94,0.3)] bg-[color:rgba(165,231,94,0.1)] px-3 py-2 text-xs leading-5 text-[var(--oh-color-success)]">
          {t(I18nKey.INTEGRATIONS_HUB$SETUP_READY, { name: integration.name })}
        </p>
      ) : null}
    </div>
  );
}
