const ENTRY_KEY = "openhands_signup_entry";
const ENTRY_TTL_MS = 30 * 60 * 1000;
const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign"] as const;

export type SignupEntrySource =
  | "marketing_website"
  | "docs"
  | "local_canvas"
  | "direct_or_unknown";
type Campaign = Partial<Record<(typeof UTM_KEYS)[number], string>>;
type Entry = {
  signup_entry_source: SignupEntrySource;
  campaign: Campaign;
  expiresAt: number;
  visitCaptured: boolean;
};

function isSource(value: unknown): value is SignupEntrySource {
  return [
    "marketing_website",
    "docs",
    "local_canvas",
    "direct_or_unknown",
  ].includes(value as string);
}

function cleanCampaign(value: unknown): string | undefined {
  return typeof value === "string" && /^[a-z0-9_:-]{1,80}$/i.test(value)
    ? value
    : undefined;
}

function campaignFrom(value: unknown): Campaign {
  if (!value || typeof value !== "object") return {};
  const source = value as Record<string, unknown>;
  return Object.fromEntries(
    UTM_KEYS.flatMap((key) => {
      const clean = cleanCampaign(source[key]);
      return clean ? [[key, clean]] : [];
    }),
  );
}

function storedEntry(): Entry | null {
  try {
    const raw = sessionStorage.getItem(ENTRY_KEY);
    if (!raw) return null;
    const entry = JSON.parse(raw) as Entry;
    if (
      !isSource(entry.signup_entry_source) ||
      !Number.isFinite(entry.expiresAt) ||
      entry.expiresAt < Date.now() ||
      entry.expiresAt > Date.now() + ENTRY_TTL_MS
    ) {
      sessionStorage.removeItem(ENTRY_KEY);
      return null;
    }
    return { ...entry, campaign: campaignFrom(entry.campaign) };
  } catch {
    return null;
  }
}

export function rememberCloudEntry(
  handoffSource?: unknown,
  handoffCampaign?: unknown,
): void {
  const params = new URLSearchParams(window.location.search);
  const campaign = {
    ...campaignFrom(handoffCampaign),
    ...campaignFrom(Object.fromEntries(params)),
  };
  const fromDevice =
    window.location.pathname === "/oauth/device/verify" &&
    params.has("user_code") &&
    params.get("signup_entry_source") === "local_canvas";
  const querySource = params.get("signup_entry_source");
  let source: SignupEntrySource | null = null;
  if (handoffSource === "marketing_website" || handoffSource === "docs") {
    source = handoffSource;
  } else if (fromDevice) {
    source = "local_canvas";
  } else if (querySource === "marketing_website" || querySource === "docs") {
    source = querySource;
  } else if (Object.keys(campaign).length) {
    source = "direct_or_unknown";
  }
  if (!source) return;
  try {
    sessionStorage.setItem(
      ENTRY_KEY,
      JSON.stringify({
        signup_entry_source: source,
        campaign,
        expiresAt: Date.now() + ENTRY_TTL_MS,
        visitCaptured: false,
      } satisfies Entry),
    );
  } catch {
    // Signup still succeeds when session storage is blocked.
  }
}

export function getSignupEntry():
  | (Campaign & { signup_entry_source: SignupEntrySource })
  | null {
  const entry = storedEntry();
  return entry
    ? { signup_entry_source: entry.signup_entry_source, ...entry.campaign }
    : null;
}

export function claimMarketingVisit(): ReturnType<typeof getSignupEntry> {
  const entry = storedEntry();
  if (
    !entry ||
    entry.visitCaptured ||
    (entry.signup_entry_source === "direct_or_unknown" &&
      !Object.keys(entry.campaign).length) ||
    entry.signup_entry_source === "local_canvas"
  )
    return null;
  try {
    sessionStorage.setItem(
      ENTRY_KEY,
      JSON.stringify({ ...entry, visitCaptured: true }),
    );
  } catch {
    return null;
  }
  return { signup_entry_source: entry.signup_entry_source, ...entry.campaign };
}

export function clearSignupEntry(): void {
  try {
    sessionStorage.removeItem(ENTRY_KEY);
  } catch {
    // Session storage is optional.
  }
}
