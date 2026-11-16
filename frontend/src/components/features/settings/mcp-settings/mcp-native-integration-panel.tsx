import React from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { CircleCheck } from "lucide-react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { BrandBadge } from "#/components/shared/badge";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useSettings } from "#/hooks/query/use-settings";
import { I18nKey } from "#/i18n/declaration";
import type { Provider } from "#/types/settings";
import { convertRawProvidersToList } from "#/utils/convert-raw-providers-to-list";
import type { McpLibraryEntry } from "#/utils/mcp-library";
import { ORG_QUERY_PARAM } from "#/utils/org/org-url-param";

interface McpNativeIntegrationPanelProps {
  entry: McpLibraryEntry;
  provider: Provider;
  isConnected: boolean;
  /** Fired once Continue confirms the connection the member just made. */
  onConnected: () => void;
}

/**
 * The "Built-in" option for GitHub, GitLab or Bitbucket when the instance
 * offers that provider. Connecting happens on Settings > Integrations (an
 * OAuth flow of its own), so Install opens it in a new tab and Continue
 * re-reads the member's connected providers. Once the provider is connected,
 * Continue goes to that page.
 */
export function McpNativeIntegrationPanel({
  entry,
  provider,
  isConnected,
  onConnected,
}: McpNativeIntegrationPanelProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { organizationId } = useSelectedOrganizationId();
  const { refetch: refetchSettings } = useSettings();
  const [hasOpenedIntegrations, setHasOpenedIntegrations] =
    React.useState(false);
  const [isChecking, setIsChecking] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const features = [t(I18nKey.MCP$NATIVE_FEATURE_REPOS, { name: entry.name })];
  if (provider === "github") {
    features.push(t(I18nKey.MCP$NATIVE_FEATURE_MENTIONS_GITHUB));
  } else if (provider === "gitlab") {
    features.push(t(I18nKey.MCP$NATIVE_FEATURE_MENTIONS_GITLAB));
  }

  const openIntegrations = () => {
    const orgQuery = organizationId
      ? `?${ORG_QUERY_PARAM}=${encodeURIComponent(organizationId)}`
      : "";
    window.open(
      `/settings/integrations${orgQuery}`,
      "_blank",
      "noopener,noreferrer",
    );
    setHasOpenedIntegrations(true);
  };

  const confirmConnection = async () => {
    setError(null);
    setIsChecking(true);
    const { data } = await refetchSettings();
    setIsChecking(false);
    if (
      convertRawProvidersToList(data?.provider_tokens_set).includes(provider)
    ) {
      onConnected();
      return;
    }
    setError(t(I18nKey.MCP$NATIVE_NOT_CONNECTED_YET, { name: entry.name }));
  };

  return (
    <div
      data-testid="mcp-native-panel"
      className="flex flex-col gap-4 rounded-xl border border-[var(--oh-border)] p-4"
    >
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-white">
            {t(I18nKey.MCP$NATIVE_TAB, { name: entry.name })}
          </h3>
          <BrandBadge className="shrink-0 whitespace-nowrap px-2.5 py-1 text-xs">
            {t(I18nKey.SETTINGS$SKILLS_RECOMMENDED)}
          </BrandBadge>
        </div>
        <p className="text-xs text-muted">
          {t(I18nKey.MCP$NATIVE_DESCRIPTION, { name: entry.name })}
        </p>
      </div>

      <ul className="flex flex-col gap-2">
        {features.map((feature) => (
          <li
            key={feature}
            className="flex items-center gap-2 text-sm text-white"
          >
            <CircleCheck
              className="h-4 w-4 shrink-0 text-[var(--oh-success)]"
              aria-hidden
            />
            {feature}
          </li>
        ))}
      </ul>

      <div className="flex items-center justify-between gap-3 border-t border-[var(--oh-border)] pt-4">
        <p className="text-xs text-muted">
          {isConnected
            ? t(I18nKey.MCP$NATIVE_CONNECTED, { name: entry.name })
            : t(I18nKey.MCP$NATIVE_CONNECT_HINT, { name: entry.name })}
        </p>
        <div className="flex shrink-0 gap-2">
          {!isConnected && (
            <BrandButton
              type="button"
              variant={hasOpenedIntegrations ? "secondary" : "primary"}
              onClick={openIntegrations}
              testId="mcp-native-install"
            >
              {t(I18nKey.MCP$INSTALL_BUTTON)}
            </BrandButton>
          )}
          {(isConnected || hasOpenedIntegrations) && (
            <BrandButton
              type="button"
              variant="primary"
              onClick={
                isConnected
                  ? () => navigate("/settings/integrations")
                  : confirmConnection
              }
              isDisabled={isChecking}
              testId="mcp-native-continue"
            >
              {t(I18nKey.BUTTON$CONTINUE)}
            </BrandButton>
          )}
        </div>
      </div>

      {error && (
        <p data-testid="mcp-native-error" className="text-sm text-red-500">
          {error}
        </p>
      )}
    </div>
  );
}
