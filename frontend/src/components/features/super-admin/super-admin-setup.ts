import type { SetupGuideSteps } from "#/api/super-admin-service/super-admin-service.api";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";
import { useSetupState } from "#/hooks/query/use-super-admin";

export type SuperAdminSetupStepId =
  | "add-llm"
  | "add-integration"
  | "first-automation"
  | "invite-users"
  | "optional-saml";

/**
 * What marks a step done: a server-derived flag from `guide_steps`, or
 * "optional" for a link that never counts toward progress.
 */
export type SuperAdminSetupStepCompletion = keyof SetupGuideSteps | "optional";

export interface SuperAdminSetupStep {
  id: SuperAdminSetupStepId;
  title: string;
  description: string;
  to: string;
  completion: SuperAdminSetupStepCompletion;
}

/**
 * Instance Super Admin NUX order (locked):
 * LLM → automation template → MCP → invite → optional SAML
 */
export const SUPER_ADMIN_SETUP_STEPS: SuperAdminSetupStep[] = [
  {
    id: "add-llm",
    title: "SUPER_ADMIN$SETUP_STEP_LLM",
    description: "SUPER_ADMIN$SETUP_STEP_LLM_HINT",
    to: "/settings/org-defaults",
    completion: "org_llm",
  },
  {
    id: "first-automation",
    title: "SUPER_ADMIN$SETUP_STEP_AUTOMATION",
    description: "SUPER_ADMIN$SETUP_STEP_AUTOMATION_HINT",
    to: "/automations/templates",
    completion: "automation",
  },
  {
    id: "add-integration",
    title: "SUPER_ADMIN$SETUP_STEP_INTEGRATION",
    description: "SUPER_ADMIN$SETUP_STEP_INTEGRATION_HINT",
    // Agent Canvas's MCP page, as in the Canvas guide. It saves to the same
    // member settings as /settings/mcp, which the step checks.
    to: "/canvas/mcp",
    completion: "mcp_server",
  },
  {
    id: "invite-users",
    title: "SUPER_ADMIN$SETUP_STEP_INVITE",
    description: "SUPER_ADMIN$SETUP_STEP_INVITE_HINT",
    to: "/settings/org-members",
    completion: "invite",
  },
  {
    id: "optional-saml",
    title: "SUPER_ADMIN$SETUP_STEP_SAML",
    description: "SUPER_ADMIN$SETUP_STEP_SAML_HINT",
    to: SUPER_ADMIN_PATHS.instance,
    completion: "optional",
  },
];

const REQUIRED_STEPS = SUPER_ADMIN_SETUP_STEPS.filter(
  (step) => step.completion !== "optional",
);

function isStepDone(
  step: SuperAdminSetupStep,
  guideSteps: SetupGuideSteps | null,
): boolean {
  if (!guideSteps || step.completion === "optional") {
    return false;
  }
  return guideSteps[step.completion];
}

/** The first required step the server does not report as done, if any. */
export function getNextSuperAdminSetupStep(
  guideSteps: SetupGuideSteps | null,
): SuperAdminSetupStep | null {
  return REQUIRED_STEPS.find((step) => !isStepDone(step, guideSteps)) ?? null;
}

/** Tell the guided tour and the guide that a step's action just succeeded. */
export function notifySuperAdminSetupStep(stepId: SuperAdminSetupStepId) {
  window.dispatchEvent(
    new CustomEvent(SUPER_ADMIN_SETUP_STEP_EVENT, { detail: { id: stepId } }),
  );
}

/**
 * Setup-guide progress for the signed-in user, derived on the server from
 * what the guide's organization really has.
 */
export function useSuperAdminSetup() {
  const { data, isSuccess, refetch } = useSetupState();
  const guideSteps = data?.guide_steps ?? null;
  const completed = new Set(
    REQUIRED_STEPS.filter((step) => isStepDone(step, guideSteps)).map(
      (step) => step.id,
    ),
  );
  const nextStep = getNextSuperAdminSetupStep(guideSteps);
  // Only the first Super Admin's guide has an organization; it stays until dismissed.
  const active = Boolean(data?.guide_org_id) && !data?.guide_dismissed;

  return {
    completed,
    nextStep,
    completedCount: completed.size,
    totalCount: REQUIRED_STEPS.length,
    progress: completed.size / REQUIRED_STEPS.length,
    isLoaded: isSuccess,
    active,
    visible: active && nextStep !== null,
    refetch,
  };
}
