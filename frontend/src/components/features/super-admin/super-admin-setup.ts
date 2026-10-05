import type { SetupGuideSteps } from "#/api/super-admin-service/super-admin-service.api";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";
import { useSetupState } from "#/hooks/query/use-super-admin";

export type SuperAdminSetupStepId =
  | "create-org"
  | "add-llm"
  | "add-integration"
  | "first-automation"
  | "invite-users"
  | "optional-saml";

/**
 * What marks a step done: a server-derived flag from `guide_steps`,
 * "guide-org" once the guide has its organization, or "optional" for a link
 * that never counts toward progress.
 */
export type SuperAdminSetupStepCompletion =
  | keyof SetupGuideSteps
  | "guide-org"
  | "optional";

export interface SuperAdminSetupStep {
  id: SuperAdminSetupStepId;
  title: string;
  description: string;
  to: string;
  completion: SuperAdminSetupStepCompletion;
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
    completion: "guide-org",
  },
  {
    id: "add-llm",
    title: "SUPER_ADMIN$SETUP_STEP_LLM",
    description: "SUPER_ADMIN$SETUP_STEP_LLM_HINT",
    to: "/settings/org-defaults",
    completion: "org_llm",
  },
  {
    id: "add-integration",
    title: "SUPER_ADMIN$SETUP_STEP_INTEGRATION",
    description: "SUPER_ADMIN$SETUP_STEP_INTEGRATION_HINT",
    to: "/settings/mcp",
    completion: "mcp_server",
  },
  {
    id: "first-automation",
    title: "SUPER_ADMIN$SETUP_STEP_AUTOMATION",
    description: "SUPER_ADMIN$SETUP_STEP_AUTOMATION_HINT",
    to: "/automations",
    completion: "automation",
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
  return step.completion === "guide-org" || guideSteps[step.completion];
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
  const nextStep =
    REQUIRED_STEPS.find((step) => !completed.has(step.id)) ?? null;
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
