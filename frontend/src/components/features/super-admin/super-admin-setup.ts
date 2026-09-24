import { useSyncExternalStore } from "react";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";

export const SUPER_ADMIN_SETUP_STORAGE_KEY = "oh-super-admin-setup";

export type SuperAdminSetupStepId =
  | "create-org"
  | "add-llm"
  | "add-integration"
  | "first-automation"
  | "invite-users"
  | "optional-saml";

export interface SuperAdminSetupStep {
  id: SuperAdminSetupStepId;
  title: string;
  description: string;
  to: string;
}

/**
 * Instance Super Admin NUX order (locked):
 * org → LLM → integration → automation → invite → optional SAML
 */
export const SUPER_ADMIN_SETUP_STEPS: SuperAdminSetupStep[] = [
  {
    id: "create-org",
    title: "SUPER_ADMIN$SETUP_STEP_ORG",
    description: "SUPER_ADMIN$SETUP_STEP_ORG_HINT",
    to: SUPER_ADMIN_PATHS.organizations,
  },
  {
    id: "add-llm",
    title: "SUPER_ADMIN$SETUP_STEP_LLM",
    description: "SUPER_ADMIN$SETUP_STEP_LLM_HINT",
    to: "/settings/org-defaults",
  },
  {
    id: "add-integration",
    title: "SUPER_ADMIN$SETUP_STEP_INTEGRATION",
    description: "SUPER_ADMIN$SETUP_STEP_INTEGRATION_HINT",
    to: "/settings/integrations-hub",
  },
  {
    id: "first-automation",
    title: "SUPER_ADMIN$SETUP_STEP_AUTOMATION",
    description: "SUPER_ADMIN$SETUP_STEP_AUTOMATION_HINT",
    to: "/automations",
  },
  {
    id: "invite-users",
    title: "SUPER_ADMIN$SETUP_STEP_INVITE",
    description: "SUPER_ADMIN$SETUP_STEP_INVITE_HINT",
    to: "/settings/org-members",
  },
  {
    id: "optional-saml",
    title: "SUPER_ADMIN$SETUP_STEP_SAML",
    description: "SUPER_ADMIN$SETUP_STEP_SAML_HINT",
    to: SUPER_ADMIN_PATHS.instance,
  },
];

const DEFAULT_COMPLETED: SuperAdminSetupStepId[] = [];

interface StoredSetup {
  completed: SuperAdminSetupStepId[];
  visible: boolean;
}

const listeners = new Set<() => void>();

function createSetupState(stored: StoredSetup) {
  const completed = new Set(stored.completed);
  const nextStep =
    SUPER_ADMIN_SETUP_STEPS.find((step) => !completed.has(step.id)) ?? null;
  return {
    completedIds: stored.completed,
    completed,
    visible: stored.visible,
    nextStep,
    completedCount: stored.completed.length,
    totalCount: SUPER_ADMIN_SETUP_STEPS.length,
    progress:
      SUPER_ADMIN_SETUP_STEPS.length === 0
        ? 0
        : stored.completed.length / SUPER_ADMIN_SETUP_STEPS.length,
  };
}

function sanitizeCompleted(value: unknown): SuperAdminSetupStepId[] {
  if (!Array.isArray(value)) {
    return DEFAULT_COMPLETED;
  }
  const validIds = new Set(SUPER_ADMIN_SETUP_STEPS.map((step) => step.id));
  return value.filter(
    (id): id is SuperAdminSetupStepId =>
      typeof id === "string" && validIds.has(id as SuperAdminSetupStepId),
  );
}

function readStored(): StoredSetup {
  if (typeof window === "undefined") {
    return { completed: DEFAULT_COMPLETED, visible: true };
  }
  try {
    const raw = window.localStorage.getItem(SUPER_ADMIN_SETUP_STORAGE_KEY);
    if (!raw) {
      return { completed: DEFAULT_COMPLETED, visible: true };
    }
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed)) {
      return { completed: sanitizeCompleted(parsed), visible: true };
    }
    if (parsed && typeof parsed === "object") {
      const record = parsed as { completed?: unknown; visible?: unknown };
      return {
        completed: sanitizeCompleted(record.completed),
        visible: record.visible !== false,
      };
    }
    return { completed: DEFAULT_COMPLETED, visible: true };
  } catch {
    return { completed: DEFAULT_COMPLETED, visible: true };
  }
}

let snapshot = createSetupState(readStored());

function notify() {
  snapshot = createSetupState(readStored());
  listeners.forEach((listener) => listener());
}

function writeStored(next: StoredSetup) {
  window.localStorage.setItem(
    SUPER_ADMIN_SETUP_STORAGE_KEY,
    JSON.stringify(next),
  );
  notify();
}

export function getSuperAdminSetupState() {
  return snapshot;
}

export function setSuperAdminSetupStepComplete(
  stepId: SuperAdminSetupStepId,
  complete: boolean,
) {
  const current = readStored();
  const completed = new Set(current.completed);
  const wasComplete = completed.has(stepId);
  if (complete) {
    completed.add(stepId);
  } else {
    completed.delete(stepId);
  }
  writeStored({
    visible: current.visible,
    completed: SUPER_ADMIN_SETUP_STEPS.map((step) => step.id).filter((id) =>
      completed.has(id),
    ),
  });
  if (complete && !wasComplete && typeof window !== "undefined") {
    window.dispatchEvent(
      new CustomEvent(SUPER_ADMIN_SETUP_STEP_EVENT, {
        detail: { id: stepId },
      }),
    );
  }
}

export function setSuperAdminSetupVisible(visible: boolean) {
  const current = readStored();
  writeStored({ ...current, visible });
}

export function resetSuperAdminSetupState() {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(SUPER_ADMIN_SETUP_STORAGE_KEY);
  }
  notify();
}

export function useSuperAdminSetup() {
  return useSyncExternalStore(
    (onStoreChange) => {
      listeners.add(onStoreChange);
      const onStorage = (event: StorageEvent) => {
        if (event.key === SUPER_ADMIN_SETUP_STORAGE_KEY) {
          notify();
        }
      };
      window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(onStoreChange);
        window.removeEventListener("storage", onStorage);
      };
    },
    getSuperAdminSetupState,
    getSuperAdminSetupState,
  );
}
