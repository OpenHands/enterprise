/**
 * First-install Super Admin NUX (before Setup Guide):
 * Welcome → TOS → Create account → /super-admin/setup
 */

export const SUPER_ADMIN_NUX_STORAGE_KEY = "oh-sa-nux";

export type SuperAdminNuxStep = "welcome" | "tos" | "account" | "done";

export interface SuperAdminNuxState {
  welcomeDone: boolean;
  tosDone: boolean;
  accountDone: boolean;
  account?: {
    name: string;
    email: string;
  };
}

const DEFAULT_STATE: SuperAdminNuxState = {
  welcomeDone: false,
  tosDone: false,
  accountDone: false,
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
      tosDone: parsed.tosDone === true,
      accountDone: parsed.accountDone === true,
      account: parsed.account,
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
  if (!state.tosDone) return "tos";
  if (!state.accountDone) return "account";
  return "done";
}

export function getSuperAdminNuxPath(
  step: SuperAdminNuxStep = getSuperAdminNuxStep(),
): string {
  switch (step) {
    case "welcome":
      return "/install";
    case "tos":
      return "/install/tos";
    case "account":
      return "/install/account";
    case "done":
      return "/super-admin/setup";
    default:
      return "/install";
  }
}

export function markSuperAdminNuxWelcomeDone() {
  const current = parseStored();
  writeSuperAdminNux({ ...current, welcomeDone: true });
}

export function markSuperAdminNuxTosDone() {
  const current = parseStored();
  writeSuperAdminNux({ ...current, tosDone: true });
}

export function markSuperAdminNuxAccountDone(account: {
  name: string;
  email: string;
}) {
  const current = parseStored();
  writeSuperAdminNux({
    ...current,
    accountDone: true,
    account: { name: account.name.trim(), email: account.email.trim() },
  });
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
        tosDone: false,
        accountDone: false,
      });
      break;
    case "tos":
      writeSuperAdminNux({
        welcomeDone: true,
        tosDone: false,
        accountDone: false,
      });
      break;
    case "account":
      writeSuperAdminNux({
        welcomeDone: true,
        tosDone: true,
        accountDone: false,
      });
      break;
    case "done":
      writeSuperAdminNux({
        welcomeDone: true,
        tosDone: true,
        accountDone: true,
        account: {
          name: "Test Super Admin",
          email: "me@acme.org",
        },
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
