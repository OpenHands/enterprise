import React from "react";
import type { BootstrapConfig } from "posthog-js";
import { PostHogProvider } from "posthog-js/react";
import { queryClient } from "#/query-client-config";
import OptionService from "#/api/option-service/option-service.api";
import { QUERY_KEYS, CONFIG_CACHE_OPTIONS } from "#/hooks/query/query-keys";
import { displayErrorToast } from "#/utils/custom-toast-handlers";

const POSTHOG_BOOTSTRAP_KEY = "posthog_bootstrap";
const POSTHOG_HANDOFF_PARAM = "oh_ph_handoff";
const consumedHandoffNonces = new Set<string>();

const ATTRIBUTION_KEYS = [
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "landing_page_category",
  "cta_id",
  "cta_surface",
  "referring_domain_category",
] as const;

type WebsiteHandoffAttribution = Partial<
  Record<(typeof ATTRIBUTION_KEYS)[number], string>
>;

type PostHogHandoff = {
  bootstrap: BootstrapConfig;
  attribution?: WebsiteHandoffAttribution;
};

type StoredHandoff = PostHogHandoff & {
  exp?: number;
  nonce?: string;
};

function safeSessionStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

function safeLocalStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function isBootstrapConfig(value: unknown): value is BootstrapConfig {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.distinctID === "string" &&
    typeof candidate.sessionID === "string"
  );
}

function base64UrlDecode(value: string): string {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  const decoded = atob(padded);
  const encoded = Array.from(
    decoded,
    (char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`,
  ).join("");
  return decodeURIComponent(encoded);
}

function sanitizeAttribution(
  value: unknown,
): WebsiteHandoffAttribution | undefined {
  if (typeof value !== "object" || value === null) return undefined;
  const source = value as Record<string, unknown>;
  const attribution: WebsiteHandoffAttribution = {};

  for (const key of ATTRIBUTION_KEYS) {
    const candidate = source[key];
    if (typeof candidate === "string" && candidate.trim()) {
      attribution[key] = candidate.trim().slice(0, 80);
    }
  }

  return Object.keys(attribution).length > 0 ? attribution : undefined;
}

function isHandoffNonceConsumed(nonce: string): boolean {
  if (consumedHandoffNonces.has(nonce)) return true;

  try {
    return (
      safeLocalStorage()?.getItem(`${POSTHOG_BOOTSTRAP_KEY}:${nonce}`) ===
      "consumed"
    );
  } catch {
    return false;
  }
}

function markHandoffNonceConsumed(nonce: string): void {
  consumedHandoffNonces.add(nonce);
  try {
    safeLocalStorage()?.setItem(
      `${POSTHOG_BOOTSTRAP_KEY}:${nonce}`,
      "consumed",
    );
  } catch {
    // In-memory replay protection still applies for this page lifetime.
  }
}

function removeHandoffFromUrl(params: URLSearchParams): void {
  params.delete(POSTHOG_HANDOFF_PARAM);
  params.delete("distinct_id");
  params.delete("session_id");
  const nextHash = params.toString();
  window.history.replaceState(
    null,
    "",
    `${window.location.pathname}${window.location.search}${nextHash ? `#${nextHash}` : ""}`,
  );
}

function parseStructuredHandoff(encoded: string): StoredHandoff | undefined {
  try {
    const parsed: unknown = JSON.parse(base64UrlDecode(encoded));
    if (typeof parsed !== "object" || parsed === null) return undefined;

    const candidate = parsed as Record<string, unknown>;
    if (candidate.v !== 1) return undefined;
    if (typeof candidate.exp !== "number" || candidate.exp < Date.now())
      return undefined;
    if (typeof candidate.nonce !== "string" || !candidate.nonce)
      return undefined;
    if (isHandoffNonceConsumed(candidate.nonce)) return undefined;
    if (typeof candidate.distinct_id !== "string" || !candidate.distinct_id)
      return undefined;
    if (typeof candidate.session_id !== "string" || !candidate.session_id)
      return undefined;

    const handoff = {
      bootstrap: {
        distinctID: candidate.distinct_id.slice(0, 256),
        sessionID: candidate.session_id.slice(0, 256),
      },
      attribution: sanitizeAttribution(candidate.attribution),
      exp: candidate.exp,
      nonce: candidate.nonce,
    };
    markHandoffNonceConsumed(candidate.nonce);
    return handoff;
  } catch {
    return undefined;
  }
}

function getHandoffFromUrl(): PostHogHandoff | null | undefined {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const structured = params.get(POSTHOG_HANDOFF_PARAM);
  const distinctID = params.get("distinct_id");
  const sessionID = params.get("session_id");
  if (!structured && !(distinctID && sessionID)) return undefined;

  const handoff = structured
    ? parseStructuredHandoff(structured)
    : {
        bootstrap: { distinctID: distinctID ?? "", sessionID: sessionID ?? "" },
      };

  if (handoff) {
    try {
      safeSessionStorage()?.setItem(
        POSTHOG_BOOTSTRAP_KEY,
        JSON.stringify(handoff),
      );
    } catch {
      // OAuth continuity is best effort when browser storage is unavailable.
    }
  }

  try {
    removeHandoffFromUrl(params);
  } catch {
    // Analytics must never block app rendering.
  }
  return handoff ?? null;
}

function isStoredHandoff(value: unknown): value is StoredHandoff {
  if (isBootstrapConfig(value)) return true;
  if (typeof value !== "object" || value === null) return false;
  return isBootstrapConfig((value as Record<string, unknown>).bootstrap);
}

function getStoredHandoff(): PostHogHandoff | undefined {
  const storage = safeSessionStorage();
  if (!storage) return undefined;

  try {
    const stored = storage.getItem(POSTHOG_BOOTSTRAP_KEY);
    if (!stored) return undefined;

    storage.removeItem(POSTHOG_BOOTSTRAP_KEY);
    const parsed: unknown = JSON.parse(stored);
    if (!isStoredHandoff(parsed)) return undefined;
    if (isBootstrapConfig(parsed)) return { bootstrap: parsed };
    if (typeof parsed.exp === "number" && parsed.exp < Date.now())
      return undefined;
    return {
      bootstrap: parsed.bootstrap,
      attribution: sanitizeAttribution(parsed.attribution),
    };
  } catch {
    try {
      storage.removeItem(POSTHOG_BOOTSTRAP_KEY);
    } catch {
      // Ignore storage failures.
    }
    return undefined;
  }
}

function getPostHogHandoff(): PostHogHandoff | undefined {
  const urlHandoff = getHandoffFromUrl();
  return urlHandoff === undefined
    ? getStoredHandoff()
    : (urlHandoff ?? undefined);
}

function registerWebsiteAttribution(
  posthog: { register: (properties: WebsiteHandoffAttribution) => void },
  attribution?: WebsiteHandoffAttribution,
): void {
  if (!attribution || Object.keys(attribution).length === 0) return;
  posthog.register(attribution);
}

export function PostHogWrapper({ children }: { children: React.ReactNode }) {
  const [posthogClientKey, setPosthogClientKey] = React.useState<string | null>(
    null,
  );
  const [isLoading, setIsLoading] = React.useState(true);
  const handoff = React.useMemo(() => getPostHogHandoff(), []);

  React.useEffect(() => {
    (async () => {
      try {
        // Use fetchQuery for automatic caching and deduplication
        const config = await queryClient.fetchQuery({
          queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
          queryFn: OptionService.getConfig,
          ...CONFIG_CACHE_OPTIONS,
        });
        setPosthogClientKey(config.posthog_client_key);
      } catch {
        displayErrorToast("Error fetching PostHog client key");
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  if (isLoading || !posthogClientKey) {
    return children;
  }

  return (
    <PostHogProvider
      apiKey={posthogClientKey}
      options={{
        api_host: "https://us.i.posthog.com",
        person_profiles: "identified_only",
        capture_performance: {
          network_timing: true,
          web_vitals: true,
        },
        capture_exceptions: true,
        bootstrap: handoff?.bootstrap,
        loaded: (posthog) =>
          registerWebsiteAttribution(posthog, handoff?.attribution),
        __add_tracing_headers: [window.location.hostname],
      }}
    >
      {children}
    </PostHogProvider>
  );
}
