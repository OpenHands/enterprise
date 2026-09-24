import type { OrganizationUserRole } from "#/types/org";
import { I18nKey } from "#/i18n/declaration";

export type SetupPersona = "super_admin" | "owner" | "admin" | "member";

export type SetupStepId =
  | "org_identity"
  | "llm"
  | "integration"
  | "automation"
  | "invite"
  | "git"
  | "llm_confirm"
  | "first_conversation"
  | "saml";

export type SetupStepKind = "org" | "role" | "personal" | "instance";

export interface SetupStepDefinition {
  id: SetupStepId;
  kind: SetupStepKind;
  /** Roles that include this step in their candidate list */
  personas: SetupPersona[];
  titleKey: I18nKey;
  descriptionKey: I18nKey;
  actionLabelKey: I18nKey;
  /** Deep-link for the step CTA */
  to: string;
  required: boolean;
}

export interface OrgSetupPredicates {
  hasOrgIdentity: boolean;
  hasLlm: boolean;
  hasIntegration: boolean;
  hasAutomation: boolean;
  hasTeammates: boolean;
  hasGit: boolean;
  hasConversation: boolean;
  hasSaml: boolean;
  setupFinished: boolean;
}

/** Candidate steps by persona (before omit-if-done). */
export const SETUP_STEPS: SetupStepDefinition[] = [
  {
    id: "org_identity",
    kind: "role",
    personas: ["owner", "super_admin"],
    titleKey: I18nKey.SETUP$STEP_ORG_IDENTITY,
    descriptionKey: I18nKey.SETUP$STEP_ORG_IDENTITY_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_ORG,
    to: "/settings/org",
    required: false,
  },
  {
    id: "llm",
    kind: "org",
    personas: ["super_admin", "owner", "admin"],
    titleKey: I18nKey.SETUP$STEP_LLM,
    descriptionKey: I18nKey.SETUP$STEP_LLM_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_LLM,
    to: "/settings/org-defaults",
    required: true,
  },
  {
    id: "integration",
    kind: "org",
    personas: ["super_admin", "owner", "admin"],
    titleKey: I18nKey.SETUP$STEP_INTEGRATION,
    descriptionKey: I18nKey.SETUP$STEP_INTEGRATION_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_INTEGRATION,
    to: "/settings/integrations-hub",
    required: true,
  },
  {
    id: "automation",
    kind: "org",
    personas: ["super_admin", "owner", "admin"],
    titleKey: I18nKey.SETUP$STEP_AUTOMATION,
    descriptionKey: I18nKey.SETUP$STEP_AUTOMATION_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_AUTOMATION,
    to: "/automations",
    required: false,
  },
  {
    id: "invite",
    kind: "org",
    personas: ["super_admin", "owner", "admin"],
    titleKey: I18nKey.SETUP$STEP_INVITE,
    descriptionKey: I18nKey.SETUP$STEP_INVITE_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_INVITE,
    to: "/settings/org-members",
    required: true,
  },
  {
    id: "saml",
    kind: "instance",
    personas: ["super_admin"],
    titleKey: I18nKey.SETUP$STEP_SAML,
    descriptionKey: I18nKey.SETUP$STEP_SAML_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_SAML,
    to: "/super-admin/instance",
    required: false,
  },
  {
    id: "git",
    kind: "personal",
    personas: ["member"],
    titleKey: I18nKey.SETUP$STEP_GIT,
    descriptionKey: I18nKey.SETUP$STEP_GIT_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_GIT,
    to: "/settings/integrations",
    required: true,
  },
  {
    id: "llm_confirm",
    kind: "personal",
    personas: ["member"],
    titleKey: I18nKey.SETUP$STEP_LLM_CONFIRM,
    descriptionKey: I18nKey.SETUP$STEP_LLM_CONFIRM_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_LLM_CONFIRM,
    to: "/settings",
    required: false,
  },
  {
    id: "first_conversation",
    kind: "personal",
    personas: ["member"],
    titleKey: I18nKey.SETUP$STEP_FIRST_CONVERSATION,
    descriptionKey: I18nKey.SETUP$STEP_FIRST_CONVERSATION_HINT,
    actionLabelKey: I18nKey.SETUP$ACTION_FIRST_CONVERSATION,
    to: "/launch",
    required: true,
  },
];

export function resolveSetupPersona({
  isInstanceSuperAdmin,
  role,
}: {
  isInstanceSuperAdmin: boolean;
  role: OrganizationUserRole;
}): SetupPersona {
  if (isInstanceSuperAdmin) {
    return "super_admin";
  }
  if (role === "owner") {
    return "owner";
  }
  if (role === "admin") {
    return "admin";
  }
  return "member";
}

export function isStepComplete(
  stepId: SetupStepId,
  predicates: OrgSetupPredicates,
): boolean {
  switch (stepId) {
    case "org_identity":
      return predicates.hasOrgIdentity;
    case "llm":
      return predicates.hasLlm;
    case "integration":
      return predicates.hasIntegration;
    case "automation":
      return predicates.hasAutomation;
    case "invite":
      return predicates.hasTeammates;
    case "git":
      return predicates.hasGit;
    case "llm_confirm":
      // Omit when org already has a usable model; otherwise keep as optional tip.
      return predicates.hasLlm;
    case "first_conversation":
      return predicates.hasConversation;
    case "saml":
      return predicates.hasSaml;
    default:
      return false;
  }
}

function deferInviteUntilCoreReady(
  candidates: SetupStepDefinition[],
  predicates: OrgSetupPredicates,
): SetupStepDefinition[] {
  const coreReady =
    predicates.hasLlm && predicates.hasIntegration && predicates.hasAutomation;
  if (coreReady) {
    return candidates;
  }
  // Keep invite in the list but after core steps; still show as remaining
  // so progress counts correctly once unlocked visually on the page.
  const withoutInvite = candidates.filter((step) => step.id !== "invite");
  const invite = candidates.find((step) => step.id === "invite");
  return invite ? [...withoutInvite, invite] : withoutInvite;
}

/**
 * Candidate steps for persona, with completed org/personal predicates
 * filtered into completed vs remaining. Required incomplete steps drive progress.
 */
export function buildSetupChecklist(
  persona: SetupPersona,
  predicates: OrgSetupPredicates,
): {
  remaining: SetupStepDefinition[];
  completed: SetupStepDefinition[];
  requiredTotal: number;
  requiredDone: number;
  nextStep: SetupStepDefinition | null;
  isCoreComplete: boolean;
  isFullyComplete: boolean;
} {
  const candidates = SETUP_STEPS.filter((step) =>
    step.personas.includes(persona),
  );

  // Super Admin invite is deferred until LLM + integration + automation are done
  const ordered =
    persona === "super_admin"
      ? deferInviteUntilCoreReady(candidates, predicates)
      : candidates;

  const completed: SetupStepDefinition[] = [];
  const remaining: SetupStepDefinition[] = [];

  for (const step of ordered) {
    if (isStepComplete(step.id, predicates)) {
      completed.push(step);
    } else {
      remaining.push(step);
    }
  }

  const requiredSteps = ordered.filter((step) => step.required);
  const requiredDone = requiredSteps.filter((step) =>
    isStepComplete(step.id, predicates),
  ).length;
  const requiredTotal = requiredSteps.length;
  const isCoreComplete = requiredDone >= requiredTotal;
  const isFullyComplete =
    predicates.setupFinished ||
    (isCoreComplete && remaining.every((step) => !step.required));

  return {
    remaining,
    completed,
    requiredTotal,
    requiredDone,
    nextStep: remaining[0] ?? null,
    isCoreComplete,
    isFullyComplete,
  };
}

export const SETUP_FINISHED_STORAGE_PREFIX = "oh-setup-finished:";
export const SETUP_AUTOMATION_STORAGE_PREFIX = "oh-setup-automation:";
export const SETUP_CONVERSATION_STORAGE_PREFIX = "oh-setup-conversation:";
export const SETUP_SAML_STORAGE_PREFIX = "oh-setup-saml:";
export const PRODUCT_TOUR_DISMISSED_KEY = "oh-product-tour-dismissed";

export function readOrgFlag(prefix: string, orgId: string | null): boolean {
  if (typeof window === "undefined" || !orgId) {
    return false;
  }
  try {
    return window.localStorage.getItem(`${prefix}${orgId}`) === "1";
  } catch {
    return false;
  }
}

export function writeOrgFlag(
  prefix: string,
  orgId: string | null,
  value: boolean,
) {
  if (typeof window === "undefined" || !orgId) {
    return;
  }
  const key = `${prefix}${orgId}`;
  if (value) {
    window.localStorage.setItem(key, "1");
  } else {
    window.localStorage.removeItem(key);
  }
}
