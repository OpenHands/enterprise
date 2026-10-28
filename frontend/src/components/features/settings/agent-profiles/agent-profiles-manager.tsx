import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentProfileScope,
  AgentProfileSummary,
} from "#/api/agent-profiles-service/agent-profiles-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useActivateAgentProfile } from "#/hooks/mutation/use-activate-agent-profile";
import { useAgentProfiles } from "#/hooks/query/use-agent-profiles";
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
}

/**
 * The member's agent-profile library, laid out like the Secrets page: the
 * member's own profiles first, then the organization's shared ones in a
 * section of their own. For most members the org section is read-only (they
 * can set one as their default) and hidden when the org has none; an admin
 * with ``canManageOrg`` sees it always, with Add, Edit and Delete.
 */
export function AgentProfilesManager({
  onAddProfile,
  onEditProfile,
  isAddDisabled = false,
  isAddOrgDisabled = isAddDisabled,
  canManageOrg = false,
}: AgentProfilesManagerProps) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useAgentProfiles();
  const activateProfile = useActivateAgentProfile();
  const [profileToDelete, setProfileToDelete] =
    useState<AgentProfileSummary | null>(null);

  const profiles = data?.profiles ?? [];
  const personalProfiles = profiles.filter(
    (profile) => profile.scope === "personal",
  );
  // A summary without a scope (an older backend) is an org profile.
  const orgProfiles = profiles.filter(
    (profile) => profile.scope !== "personal",
  );
  const activeId = data?.active_agent_profile_id ?? null;

  const handleActivate = async (profile: AgentProfileSummary) => {
    if (!profile.id) return;
    await mutateWithToast(activateProfile, profile.id, {
      success: t(I18nKey.SETTINGS$PROFILE_ACTIVATED, { name: profile.name }),
      error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
    }).catch(() => null);
  };

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
            onEdit={onEditProfile}
            onDelete={setProfileToDelete}
            isActivating={activateProfile.isPending}
          />
        </div>

        {!isLoading && !error && (orgProfiles.length > 0 || canManageOrg) && (
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
                  {t(
                    canManageOrg
                      ? I18nKey.SETTINGS$ORG_AGENT_PROFILES_ADMIN_DESCRIPTION
                      : I18nKey.SETTINGS$ORG_AGENT_PROFILES_DESCRIPTION,
                  )}
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
              onEdit={onEditProfile}
              onDelete={setProfileToDelete}
              isActivating={activateProfile.isPending}
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
