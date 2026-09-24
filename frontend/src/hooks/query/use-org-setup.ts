import { useMemo, useSyncExternalStore } from "react";
import { useMe } from "#/hooks/query/use-me";
import { useSettings } from "#/hooks/query/use-settings";
import { useOrganizationMembersCount } from "#/hooks/query/use-organization-members-count";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import { useUserProviders } from "#/hooks/use-user-providers";
import { isInstanceSuperAdmin } from "#/utils/org/permissions";
import {
  SETUP_AUTOMATION_STORAGE_PREFIX,
  SETUP_CONVERSATION_STORAGE_PREFIX,
  SETUP_FINISHED_STORAGE_PREFIX,
  SETUP_SAML_STORAGE_PREFIX,
  buildSetupChecklist,
  readOrgFlag,
  resolveSetupPersona,
  writeOrgFlag,
  type OrgSetupPredicates,
} from "#/utils/org/setup-readiness";
import {
  isSetupTestHarnessEnabled,
  readSetupTestPersona,
  resolveTestSetupPersona,
  subscribeSetupTestPersona,
} from "#/utils/org/setup-test-harness";

const setupListeners = new Set<() => void>();

function notifySetupStorage() {
  setupListeners.forEach((listener) => listener());
}

function subscribeSetupStorage(onStoreChange: () => void) {
  setupListeners.add(onStoreChange);
  const onStorage = (event: StorageEvent) => {
    if (
      event.key?.startsWith("oh-setup-") ||
      event.key === "oh-product-tour-dismissed"
    ) {
      onStoreChange();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    setupListeners.delete(onStoreChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function markSetupAutomationComplete(orgId: string | null) {
  writeOrgFlag(SETUP_AUTOMATION_STORAGE_PREFIX, orgId, true);
  notifySetupStorage();
}

export function markSetupFinished(orgId: string | null) {
  writeOrgFlag(SETUP_FINISHED_STORAGE_PREFIX, orgId, true);
  notifySetupStorage();
}

export function markSetupSamlComplete(orgId: string | null) {
  writeOrgFlag(SETUP_SAML_STORAGE_PREFIX, orgId, true);
  notifySetupStorage();
}

export function markFirstConversationComplete(orgId: string | null) {
  writeOrgFlag(SETUP_CONVERSATION_STORAGE_PREFIX, orgId, true);
  notifySetupStorage();
}

/**
 * Org/user setup readiness for Getting Started page + widget.
 * Org predicates prefer live data; automation/SAML/finish use local flags until APIs exist.
 */
export function useOrgSetup() {
  const { data: me } = useMe();
  const { data: settings } = useSettings();
  const { organizationId, isTeamOrg, isPersonalOrg, selectedOrg } =
    useOrgTypeAndAccess();
  const { providers } = useUserProviders();
  const { data: membersCount } = useOrganizationMembersCount();

  useSyncExternalStore(
    subscribeSetupStorage,
    () =>
      [
        readOrgFlag(SETUP_FINISHED_STORAGE_PREFIX, organizationId),
        readOrgFlag(SETUP_AUTOMATION_STORAGE_PREFIX, organizationId),
        readOrgFlag(SETUP_SAML_STORAGE_PREFIX, organizationId),
        readOrgFlag(SETUP_CONVERSATION_STORAGE_PREFIX, organizationId),
      ].join(":"),
    () => "ssr",
  );

  useSyncExternalStore(
    subscribeSetupTestPersona,
    readSetupTestPersona,
    () => "live",
  );

  const role = me?.role ?? "member";
  const livePersona = resolveSetupPersona({
    isInstanceSuperAdmin: isInstanceSuperAdmin(me?.permissions),
    role,
  });
  const persona = resolveTestSetupPersona(livePersona);

  const predicates: OrgSetupPredicates = useMemo(() => {
    // Prefer api-key-set over mapped llm_model — empty installs still resolve
    // to DEFAULT_SETTINGS.llm_model in useSettings.
    const hasLlm = settings?.llm_api_key_set === true;
    const hasIntegration = providers.length > 0;
    const hasTeammates = (membersCount ?? 0) > 1;
    const orgName = selectedOrg?.name ?? "";
    const hasOrgIdentity =
      Boolean(orgName.trim()) &&
      !/^personal$/i.test(orgName.trim()) &&
      orgName.trim().length > 2;

    return {
      hasOrgIdentity,
      hasLlm,
      hasIntegration,
      hasAutomation: readOrgFlag(
        SETUP_AUTOMATION_STORAGE_PREFIX,
        organizationId,
      ),
      hasTeammates,
      hasGit: hasIntegration,
      hasConversation: readOrgFlag(
        SETUP_CONVERSATION_STORAGE_PREFIX,
        organizationId,
      ),
      hasSaml: readOrgFlag(SETUP_SAML_STORAGE_PREFIX, organizationId),
      setupFinished: readOrgFlag(SETUP_FINISHED_STORAGE_PREFIX, organizationId),
    };
  }, [
    settings?.llm_api_key_set,
    providers.length,
    membersCount,
    selectedOrg?.name,
    organizationId,
  ]);

  const checklist = useMemo(
    () => buildSetupChecklist(persona, predicates),
    [persona, predicates],
  );

  const progress =
    checklist.requiredTotal === 0
      ? 1
      : checklist.requiredDone / checklist.requiredTotal;

  // Show while there is remaining setup work for this persona/org.
  // Persona override (mock harness) can force the Getting Started surface
  // even when the live user is a Super Admin.
  const showSetup =
    Boolean(organizationId) &&
    !predicates.setupFinished &&
    (isTeamOrg ||
      persona === "member" ||
      isPersonalOrg ||
      (isSetupTestHarnessEnabled() && persona !== "super_admin")) &&
    checklist.remaining.length > 0;

  return {
    persona,
    role,
    organizationId,
    isTeamOrg,
    isPersonalOrg,
    predicates,
    checklist,
    progress,
    showSetup,
    markFinished: () => markSetupFinished(organizationId),
    markAutomationDone: () => markSetupAutomationComplete(organizationId),
    markSamlDone: () => markSetupSamlComplete(organizationId),
  };
}
