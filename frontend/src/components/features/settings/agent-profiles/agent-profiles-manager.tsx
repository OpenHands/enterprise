import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentProfileScope,
  AgentProfileSummary,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useActivateAgentProfile } from "#/hooks/mutation/use-activate-agent-profile";
import { useDeactivateAgentProfile } from "#/hooks/mutation/use-deactivate-agent-profile";
import { useAgentProfiles } from "#/hooks/query/use-agent-profiles";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";
import { I18nKey } from "#/i18n/declaration";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { mutateWithToast } from "#/utils/mutate-with-toast";
import { AgentProfilesBody } from "./agent-profiles-body";
import { DeleteAgentProfileModal } from "./delete-agent-profile-modal";

interface AgentProfilesManagerProps {
  onAddProfile: (scope: AgentProfileScope) => void;
  onEditProfile: (profile: AgentProfileSummary) => void;
  /** True until the editor's inputs (settings, schema) have loaded. */
  isAddDisabled?: boolean;
  /** True until the org editor's inputs (the org's shared secrets too) have loaded. */
  isAddOrgDisabled?: boolean;
  /** True when the viewer may add, edit and delete the organization's profiles. */
  canManageOrg?: boolean;
  /** ``feature_flags.enable_org_mcp_servers``: org profiles may name
   * organization-managed MCP servers, so the admin copy says so (OHE-3091). */
  orgMcpServersEnabled?: boolean;
}

/**
 * The member's agent-profile library, laid out like the Secrets page: the
 * member's own profiles first, then the organization's shared ones in a
 * section of their own. For most members the org section is read-only (they
 * can set one as their default) and hidden when the org has none; an admin
 * with ``canManageOrg`` sees it always, with Add, Edit and Delete. A personal
 * workspace has no organization to share with, so it shows a single list
 * (OHE-3864).
 */
export function AgentProfilesManager({
  onAddProfile,
  onEditProfile,
  isAddDisabled = false,
  isAddOrgDisabled = isAddDisabled,
  canManageOrg = false,
  orgMcpServersEnabled = false,
}: AgentProfilesManagerProps) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useAgentProfiles();
  const activateProfile = useActivateAgentProfile();
  const deactivateProfile = useDeactivateAgentProfile();
  const [profileToDelete, setProfileToDelete] =
    useState<AgentProfileSummary | null>(null);

  const { isTeamOrg } = useOrgTypeAndAccess();
  const profiles = data?.profiles ?? [];
  // In a personal workspace the profiles stored on its org collection (Canvas
  // saves there, as it sends no scope) are the user's own: list them all here.
  const personalProfiles = isTeamOrg
    ? profiles.filter((profile) => profile.scope === "personal")
    : profiles;
  // A summary without a scope (an older backend) is an org profile.
  const orgProfiles = isTeamOrg
    ? profiles.filter((profile) => profile.scope !== "personal")
    : [];
  const showOrgSection = isTeamOrg && (orgProfiles.length > 0 || canManageOrg);
  const activeId = data?.active_agent_profile_id ?? null;

  let orgSectionDescriptionKey =
    I18nKey.SETTINGS$ORG_AGENT_PROFILES_DESCRIPTION;
  if (canManageOrg) {
    orgSectionDescriptionKey = orgMcpServersEnabled
      ? I18nKey.SETTINGS$ORG_AGENT_PROFILES_ADMIN_DESCRIPTION_WITH_MCP
      : I18nKey.SETTINGS$ORG_AGENT_PROFILES_ADMIN_DESCRIPTION;
  }

  const handleActivate = async (profile: AgentProfileSummary) => {
    if (!profile.id) return;
    await mutateWithToast(activateProfile, profile.id, {
      success: t(I18nKey.SETTINGS$PROFILE_ACTIVATED, { name: profile.name }),
      error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
    }).catch(() => null);
  };

  const handleDeactivate = async (profile: AgentProfileSummary) => {
    if (!profile.id) return;
    await mutateWithToast(deactivateProfile, profile.id, {
      success: t(I18nKey.SETTINGS$PROFILE_DEACTIVATED),
      error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
    }).catch(() => null);
  };
  const isActivating = activateProfile.isPending || deactivateProfile.isPending;

  return (
    <>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-white">
              {t(I18nKey.SETTINGS$PERSONAL_AGENT_PROFILES_TITLE)}
            </h2>
            <BrandButton
              testId="add-agent-profile"
              type="button"
              variant="primary"
              className="ml-auto"
              onClick={() => onAddProfile("personal")}
              isDisabled={isAddDisabled}
            >
              {t(I18nKey.SETTINGS$ADD_AGENT_PROFILE)}
            </BrandButton>
          </div>

          <AgentProfilesBody
            isLoading={isLoading}
            loadError={error ?? null}
            profiles={personalProfiles}
            activeId={activeId}
            onActivate={handleActivate}
            onDeactivate={handleDeactivate}
            onEdit={onEditProfile}
            onDelete={setProfileToDelete}
            isActivating={isActivating}
            canManageOrg={canManageOrg}
          />
        </div>

        {!isLoading && !error && showOrgSection && (
          <section
            data-testid="org-agent-profiles-section"
            className="flex flex-col gap-4 border-t border-[var(--oh-border)] pt-6"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <header className="min-w-0 space-y-1">
                <h2 className="text-base font-semibold text-white">
                  {t(I18nKey.SETTINGS$ORG_AGENT_PROFILES_TITLE)}
                </h2>
                <p className="text-sm leading-5 text-muted">
                  {t(orgSectionDescriptionKey)}
                </p>
              </header>
              {canManageOrg ? (
                <BrandButton
                  testId="add-org-agent-profile"
                  type="button"
                  variant="primary"
                  className="ml-auto"
                  onClick={() => onAddProfile("org")}
                  isDisabled={isAddOrgDisabled}
                >
                  {t(I18nKey.SETTINGS$ADD_ORG_AGENT_PROFILE)}
                </BrandButton>
              ) : null}
            </div>
            <AgentProfilesBody
              isLoading={false}
              loadError={null}
              profiles={orgProfiles}
              activeId={activeId}
              onActivate={handleActivate}
              onDeactivate={handleDeactivate}
              onEdit={onEditProfile}
              onDelete={setProfileToDelete}
              isActivating={isActivating}
              canManageOrg={canManageOrg}
            />
          </section>
        )}
      </div>

      <DeleteAgentProfileModal
        profile={profileToDelete}
        onClose={() => setProfileToDelete(null)}
      />
    </>
  );
}
