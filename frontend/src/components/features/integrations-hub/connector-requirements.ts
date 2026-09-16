import type {
  HubConnectorProvider,
  HubIntegration,
  HubTool,
} from "#/types/integrations-hub";

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

export type ConnectorConnectMethod = "oauth" | "api_key";

export interface ConnectorAuthRequirements {
  isOAuth: boolean;
  isApiKey: boolean;
  isDynamicOAuth: boolean;
  usesDynamicRegistration: boolean;
  needsManualOAuthClient: boolean;
  oauthMcp: boolean;
  needsConnect: boolean;
  connectMethod: ConnectorConnectMethod;
}

export const STUB_INDEXED_TOOL: HubTool = {
  name: "example_tool",
  description: "Indexed from the connector schema in this preview.",
  accessMode: "enabled",
  defaultScopes: [],
};

export function createConfigForm(
  integration: HubIntegration,
): ConnectorConfigForm {
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

export function isOAuthMcp(
  integration: HubIntegration,
  provider: HubConnectorProvider,
) {
  return (
    integration.authStrategy === "oauth2" &&
    provider === "mcp" &&
    !integration.openApiUrl
  );
}

export function getConnectorAuthRequirements(
  integration: HubIntegration,
  provider: HubConnectorProvider = createConfigForm(integration).provider,
): ConnectorAuthRequirements {
  const isOAuth = integration.authStrategy === "oauth2";
  const isApiKey = integration.authStrategy === "api_key";
  const isDynamicOAuth = isOAuth && !integration.oauthConfig?.authorizationUrl;
  const usesDynamicRegistration =
    isOAuth &&
    integration.oauthConfig?.clientAuthentication === "none" &&
    Boolean(integration.oauthConfig?.registrationUrl);
  const needsManualOAuthClient =
    isOAuth && !usesDynamicRegistration && !isDynamicOAuth;
  const oauthMcp = isOAuthMcp(integration, provider);

  return {
    isOAuth,
    isApiKey,
    isDynamicOAuth,
    usesDynamicRegistration,
    needsManualOAuthClient,
    oauthMcp,
    needsConnect: oauthMcp || isApiKey,
    connectMethod: isApiKey ? "api_key" : "oauth",
  };
}

export function resolveIndexedTools(
  integration: HubIntegration,
  availableTools?: HubTool[],
): HubTool[] {
  if (availableTools && availableTools.length > 0) {
    return availableTools;
  }
  if (integration.tools.length > 0) {
    return integration.tools;
  }
  return [STUB_INDEXED_TOOL];
}
