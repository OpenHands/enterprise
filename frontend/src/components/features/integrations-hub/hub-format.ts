import { I18nKey } from "#/i18n/declaration";
import type {
  HubAuthStrategy,
  HubIntegration,
  HubToolAccessMode,
  HubUserRequest,
} from "#/types/integrations-hub";

const ACCESS_MODE_RANK: Record<HubToolAccessMode, number> = {
  disabled: 0,
  approval: 1,
  enabled: 2,
};

export const HUB_ACCESS_MODES: HubToolAccessMode[] = [
  "enabled",
  "approval",
  "disabled",
];

export function clampHubAccessMode(
  requested: HubToolAccessMode,
  maximum?: HubToolAccessMode,
): HubToolAccessMode {
  if (!maximum) {
    return requested;
  }
  return ACCESS_MODE_RANK[requested] <= ACCESS_MODE_RANK[maximum]
    ? requested
    : maximum;
}

export function allowedHubAccessModes(
  maximum?: HubToolAccessMode,
): HubToolAccessMode[] {
  return HUB_ACCESS_MODES.filter(
    (mode) => clampHubAccessMode(mode, maximum) === mode,
  );
}

export function hubAuthLabel(
  strategy: HubAuthStrategy,
  t: (key: I18nKey) => string,
) {
  if (strategy === "oauth2") {
    return t(I18nKey.INTEGRATIONS_HUB$AUTH_OAUTH);
  }
  if (strategy === "api_key") {
    return t(I18nKey.INTEGRATIONS_HUB$AUTH_API_KEY);
  }
  return "";
}

export function hubIntegrationFromUserRequest(
  request: HubUserRequest,
): HubIntegration {
  const isCustom = request.source === "custom";
  return {
    slug: request.slug,
    name: request.name,
    description: request.description ?? "",
    connected: false,
    enabled: false,
    authStrategy: isCustom ? "api_key" : "oauth2",
    toolCount: 0,
    provider: isCustom ? "Custom" : "MCP",
    kind: isCustom ? "Custom" : "",
    tools: [],
    docsUrl: request.docsUrl,
    notes: request.notes,
  };
}

export function hubIntegrationMetaPills(integration: HubIntegration): string[] {
  return [integration.provider, integration.kind].filter(
    (label, index, labels) =>
      Boolean(label) &&
      label.toLowerCase() !== integration.name.toLowerCase() &&
      labels.indexOf(label) === index,
  );
}

export function formatHubTimestamp(value?: string): {
  date: string;
  time: string;
} | null {
  if (!value) {
    return null;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return null;
  }
  return {
    date: date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    }),
    time: date.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    }),
  };
}

export function formatHubDurationHours(requestedMinutes: number): string {
  return `${Math.max(1, Math.round(requestedMinutes / 60))}h`;
}

export function unusedWindowMs(
  value: number,
  unit: "days" | "weeks" | "months",
): number {
  const dayMs = 24 * 60 * 60 * 1000;
  if (unit === "weeks") {
    return value * 7 * dayMs;
  }
  if (unit === "months") {
    return value * 30 * dayMs;
  }
  return value * dayMs;
}

export function maskHubApiKey(value: string, revealed: boolean): string {
  if (revealed) {
    return value;
  }
  if (value.length <= 8) {
    return "••••••••";
  }
  return `${value.slice(0, 4)}••••${value.slice(-4)}`;
}
