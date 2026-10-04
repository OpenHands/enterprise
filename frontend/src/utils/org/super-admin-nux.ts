/**
 * First-install Super Admin NUX (before the group setup modal):
 * Welcome → Company and plan → Name first org → LLM settings
 */

export const SUPER_ADMIN_NUX_STORAGE_KEY = "oh-sa-nux";

/** Org LLM defaults, where first-install completion lands. */
export const SUPER_ADMIN_NUX_LLM_PATH = "/settings/org-defaults";

export type SuperAdminNuxStep = "welcome" | "company" | "org" | "done";

export interface SuperAdminNuxState {
  welcomeDone: boolean;
  companyDone: boolean;
  orgDone: boolean;
  /** Show the group setup intro the first time the LLM screen opens. */
  starterModalPending: boolean;
  company?: {
    name: string;
    /** True when they continued with a license key (30-day trial). */
    hasLicenseKey: boolean;
    /** True when they chose a company image. */
    hasLogo: boolean;
  };
  org?: {
    name: string;
  };
}

const DEFAULT_STATE: SuperAdminNuxState = {
  welcomeDone: false,
  companyDone: false,
  orgDone: false,
  starterModalPending: false,
};

const listeners = new Set<() => void>();

function parseStored(): SuperAdminNuxState {
  if (typeof window === "undefined") {
    return { ...DEFAULT_STATE };
  }
  try {
    const raw = window.localStorage.getItem(SUPER_ADMIN_NUX_STORAGE_KEY);
    if (!raw) {
      return { ...DEFAULT_STATE };
    }
    const parsed = JSON.parse(raw) as Partial<SuperAdminNuxState>;
    return {
      welcomeDone: parsed.welcomeDone === true,
      companyDone: parsed.companyDone === true,
      orgDone: parsed.orgDone === true,
      starterModalPending: parsed.starterModalPending === true,
      company: parsed.company,
      org: parsed.org,
    };
  } catch {
    return { ...DEFAULT_STATE };
  }
}

let snapshot = parseStored();

function notify() {
  snapshot = parseStored();
  listeners.forEach((l) => l());
}

export function readSuperAdminNux(): SuperAdminNuxState {
  return snapshot;
}

function writeSuperAdminNux(next: SuperAdminNuxState) {
  window.localStorage.setItem(
    SUPER_ADMIN_NUX_STORAGE_KEY,
    JSON.stringify(next),
  );
  notify();
}

export function getSuperAdminNuxStep(
  state = readSuperAdminNux(),
): SuperAdminNuxStep {
  if (!state.welcomeDone) return "welcome";
  if (!state.companyDone) return "company";
  if (!state.orgDone) return "org";
  return "done";
}

export function getSuperAdminNuxPath(
  step: SuperAdminNuxStep = getSuperAdminNuxStep(),
  state = readSuperAdminNux(),
): string {
  switch (step) {
    case "welcome":
      return "/install";
    case "company":
      return "/install/company";
    case "org":
      return "/install/org";
    case "done":
      return state.starterModalPending
        ? SUPER_ADMIN_NUX_LLM_PATH
        : "/super-admin/setup";
    default:
      return "/install";
  }
}

export function markSuperAdminNuxWelcomeDone() {
  const current = parseStored();
  writeSuperAdminNux({ ...current, welcomeDone: true });
}

export function markSuperAdminNuxCompanyDone(company: {
  name: string;
  hasLicenseKey: boolean;
  hasLogo?: boolean;
}) {
  const current = parseStored();
  writeSuperAdminNux({
    ...current,
    companyDone: true,
    company: {
      name: company.name.trim(),
      hasLicenseKey: company.hasLicenseKey,
      hasLogo: company.hasLogo === true,
    },
  });
}

export function markSuperAdminNuxOrgDone(org: { name: string }) {
  const current = parseStored();
  writeSuperAdminNux({
    ...current,
    orgDone: true,
    starterModalPending: true,
    org: { name: org.name.trim() },
  });
}

export function clearSuperAdminNuxStarterModal() {
  const current = parseStored();
  if (!current.starterModalPending) {
    return;
  }
  writeSuperAdminNux({ ...current, starterModalPending: false });
}

export function resetSuperAdminNux() {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(SUPER_ADMIN_NUX_STORAGE_KEY);
  }
  notify();
}

/**
 * Jump the install NUX to a specific step (testing / mock harness).
 * Sets prior steps complete so install redirect guards allow the target page.
 */
export function setSuperAdminNuxStep(step: SuperAdminNuxStep) {
  switch (step) {
    case "welcome":
      writeSuperAdminNux({
        welcomeDone: false,
        companyDone: false,
        orgDone: false,
        starterModalPending: false,
      });
      break;
    case "company":
      writeSuperAdminNux({
        welcomeDone: true,
        companyDone: false,
        orgDone: false,
        starterModalPending: false,
      });
      break;
    case "org":
      writeSuperAdminNux({
        welcomeDone: true,
        companyDone: true,
        orgDone: false,
        starterModalPending: false,
        company: { name: "Acme", hasLicenseKey: false, hasLogo: false },
      });
      break;
    case "done":
      writeSuperAdminNux({
        welcomeDone: true,
        companyDone: true,
        orgDone: true,
        starterModalPending: false,
        company: { name: "Acme", hasLicenseKey: false, hasLogo: false },
        org: { name: "My Organization" },
      });
      break;
    default:
      break;
  }
}

export function subscribeSuperAdminNux(onStoreChange: () => void) {
  listeners.add(onStoreChange);
  const onStorage = (event: StorageEvent) => {
    if (event.key === SUPER_ADMIN_NUX_STORAGE_KEY) {
      notify();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(onStoreChange);
    window.removeEventListener("storage", onStorage);
  };
}
