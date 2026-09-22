import { useTranslation } from "react-i18next";
import { SettingsInput } from "#/components/features/settings/settings-input";
import {
  HubModal,
  hubModalBodyClassName,
} from "#/components/features/integrations-hub/hub-modal";
import { I18nKey } from "#/i18n/declaration";
import { settingsListContainerClassName } from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

const HUB_API_ORIGIN =
  typeof window !== "undefined"
    ? `${window.location.origin}/api/integrations-hub`
    : "/api/integrations-hub";

const MCP_ENDPOINT = `${HUB_API_ORIGIN}/mcp`;
const OPENAPI_SCHEMA = `${HUB_API_ORIGIN}/agent/openapi`;
const USER_OPENAPI_SCHEMA = `${HUB_API_ORIGIN}/user/openapi`;
const ADMIN_OPENAPI_SCHEMA = `${HUB_API_ORIGIN}/admin/openapi`;
const MCP_TOOL = "integrations_hub.slack.post_message";
const MCP_CONFIG = `{
  "mcpServers": {
    "openhands": {
      "url": "${MCP_ENDPOINT}"
    }
  }
}`;
const AGENT_PROMPT =
  "Use the OpenHands Integrations Hub OpenAPI schema and authenticate with the agent key.";
const USER_PROMPT =
  "Use the user automation key with the dashboard-equivalent OpenAPI schema.";
const ADMIN_PROMPT =
  "Use the admin automation key with the admin-only OpenAPI schema.";

function Subsection({
  title,
  description,
  testId,
  children,
}: {
  title: string;
  description: string;
  testId: string;
  children: React.ReactNode;
}) {
  return (
    <section
      data-testid={testId}
      className="flex flex-col gap-4 border-t border-[var(--oh-border)] pt-6 pb-6 first:border-t-0 first:pt-0 last:pb-0"
    >
      <div>
        <h3 className="text-sm font-medium text-white">{title}</h3>
        <p className="mt-1 text-sm leading-5 text-tertiary-light">
          {description}
        </p>
      </div>
      {children}
    </section>
  );
}

function CodeExample({ label, children }: { label: string; children: string }) {
  return (
    <label className="grid gap-1.5 text-sm text-white">
      <span className="font-medium">{label}</span>
      <pre className="overflow-x-auto rounded-lg border border-[var(--oh-border)] bg-black/20 p-3 font-mono text-xs text-tertiary-light">
        {children}
      </pre>
    </label>
  );
}

export function ConnectionDetailsModal({
  isAdmin = false,
  onClose,
}: {
  isAdmin?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  return (
    <HubModal
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$CONNECTION_DETAILS_TITLE)}
      testId="agent-connection-details-modal"
      width="lg"
      className="min-h-0 max-h-[85vh] gap-0"
      onClose={onClose}
    >
      <header className="relative shrink-0 px-6 pb-4 pt-6 pr-12">
        <h2 className="text-lg font-medium text-white">
          {t(I18nKey.INTEGRATIONS_HUB$CONNECTION_DETAILS_TITLE)}
        </h2>
        <p className="mt-1 text-sm leading-5 text-tertiary-light">
          {t(I18nKey.INTEGRATIONS_HUB$CONNECTION_DETAILS_BODY)}
        </p>
      </header>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 pb-6">
        <div
          className={cn(
            settingsListContainerClassName,
            "flex min-h-0 flex-1 flex-col",
          )}
        >
          <div className={cn(hubModalBodyClassName, "px-4 py-4")}>
            <Subsection
              testId="agent-connection-mcp-section"
              title={t(I18nKey.INTEGRATIONS_HUB$AGENT_MCP_TITLE)}
              description={t(I18nKey.INTEGRATIONS_HUB$AGENT_MCP_DESC)}
            >
              <SettingsInput
                testId="agent-connection-mcp-endpoint"
                label={t(I18nKey.INTEGRATIONS_HUB$MCP_ENDPOINT)}
                type="text"
                value={MCP_ENDPOINT}
                isDisabled
              />
              <SettingsInput
                testId="agent-connection-mcp-example-tool"
                label={t(I18nKey.INTEGRATIONS_HUB$EXAMPLE_TOOL)}
                type="text"
                value={MCP_TOOL}
                isDisabled
              />
              <CodeExample
                label={t(I18nKey.INTEGRATIONS_HUB$MCP_CONFIG_EXAMPLE)}
              >
                {MCP_CONFIG}
              </CodeExample>
            </Subsection>
            <Subsection
              testId="agent-connection-agent-rest-section"
              title={t(I18nKey.INTEGRATIONS_HUB$AGENT_REST_TITLE)}
              description={t(I18nKey.INTEGRATIONS_HUB$AGENT_REST_DESC)}
            >
              <SettingsInput
                testId="agent-connection-agent-openapi"
                label={t(I18nKey.INTEGRATIONS_HUB$OPENAPI_SCHEMA)}
                type="text"
                value={OPENAPI_SCHEMA}
                isDisabled
              />
              <CodeExample label={t(I18nKey.INTEGRATIONS_HUB$EXAMPLE_PROMPT)}>
                {AGENT_PROMPT}
              </CodeExample>
            </Subsection>
            <Subsection
              testId="agent-connection-user-rest-section"
              title={t(I18nKey.INTEGRATIONS_HUB$USER_REST_TITLE)}
              description={t(I18nKey.INTEGRATIONS_HUB$USER_REST_DESC)}
            >
              <SettingsInput
                testId="agent-connection-user-openapi"
                label={t(I18nKey.INTEGRATIONS_HUB$OPENAPI_SCHEMA)}
                type="text"
                value={USER_OPENAPI_SCHEMA}
                isDisabled
              />
              <CodeExample label={t(I18nKey.INTEGRATIONS_HUB$EXAMPLE_PROMPT)}>
                {USER_PROMPT}
              </CodeExample>
            </Subsection>
            {isAdmin ? (
              <Subsection
                testId="agent-connection-admin-rest-section"
                title={t(I18nKey.INTEGRATIONS_HUB$ADMIN_REST_TITLE)}
                description={t(I18nKey.INTEGRATIONS_HUB$ADMIN_REST_DESC)}
              >
                <SettingsInput
                  testId="agent-connection-admin-openapi"
                  label={t(I18nKey.INTEGRATIONS_HUB$OPENAPI_SCHEMA)}
                  type="text"
                  value={ADMIN_OPENAPI_SCHEMA}
                  isDisabled
                />
                <CodeExample label={t(I18nKey.INTEGRATIONS_HUB$EXAMPLE_PROMPT)}>
                  {ADMIN_PROMPT}
                </CodeExample>
              </Subsection>
            ) : null}
          </div>
        </div>
      </div>
    </HubModal>
  );
}
