/**
 * TEMPORARY mock/dev harness for Setup + NUX QA.
 * Gated by VITE_MOCK_API — remove before production.
 */

import type { SetupPersona } from "#/utils/org/setup-readiness";

export const SETUP_TEST_PERSONA_STORAGE_KEY = "oh-setup-test-persona";

export type SetupTestPersona = SetupPersona | "live";

const PERSONAS: SetupTestPersona[] = [
  "live",
  "super_admin",
  "owner",
  "admin",
  "member",
];

const listeners = new Set<() => void>();

export function isSetupTestHarnessEnabled(): boolean {
  // Panel + overrides are mock/dev only. Vitest uses MODE=test so unit
  // tests can exercise the override helpers without a mock SaaS boot.
  return (
    import.meta.env.VITE_MOCK_API === "true" || import.meta.env.MODE === "test"
  );
}

function parsePersona(value: string | null): SetupTestPersona {
  if (value && PERSONAS.includes(value as SetupTestPersona)) {
    return value as SetupTestPersona;
  }
  return "live";
}

let snapshot: SetupTestPersona = "live";

function readFromStorage(): SetupTestPersona {
  if (typeof window === "undefined" || !isSetupTestHarnessEnabled()) {
    return "live";
  }
  try {
    return parsePersona(
      window.localStorage.getItem(SETUP_TEST_PERSONA_STORAGE_KEY),
    );
  } catch {
    return "live";
  }
}

snapshot = readFromStorage();

function notify() {
  snapshot = readFromStorage();
  listeners.forEach((listener) => listener());
}

export function readSetupTestPersona(): SetupTestPersona {
  return snapshot;
}

export function setSetupTestPersona(persona: SetupTestPersona) {
  if (!isSetupTestHarnessEnabled() || typeof window === "undefined") {
    return;
  }
  if (persona === "live") {
    window.localStorage.removeItem(SETUP_TEST_PERSONA_STORAGE_KEY);
  } else {
    window.localStorage.setItem(SETUP_TEST_PERSONA_STORAGE_KEY, persona);
  }
  notify();
}

export function subscribeSetupTestPersona(onStoreChange: () => void) {
  listeners.add(onStoreChange);
  const onStorage = (event: StorageEvent) => {
    if (event.key === SETUP_TEST_PERSONA_STORAGE_KEY) {
      notify();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("storage", onStorage);
  };
}

/** Apply an optional QA override on top of the live-resolved persona. */
export function resolveTestSetupPersona(
  livePersona: SetupPersona,
): SetupPersona {
  if (!isSetupTestHarnessEnabled()) {
    return livePersona;
  }
  const override = readSetupTestPersona();
  return override === "live" ? livePersona : override;
}

/**
 * When a persona override is active, force SA chrome on/off to match.
 * `null` means "use live permissions".
 */
export function getSetupTestSuperAdminAccessOverride(): boolean | null {
  if (!isSetupTestHarnessEnabled()) {
    return null;
  }
  const override = readSetupTestPersona();
  if (override === "live") {
    return null;
  }
  return override === "super_admin";
}

export const SETUP_TEST_PERSONA_OPTIONS: {
  id: SetupTestPersona;
  label: string;
}[] = [
  { id: "live", label: "Live" },
  { id: "super_admin", label: "Super Admin" },
  { id: "owner", label: "Owner" },
  { id: "admin", label: "Admin" },
  { id: "member", label: "Member" },
];
