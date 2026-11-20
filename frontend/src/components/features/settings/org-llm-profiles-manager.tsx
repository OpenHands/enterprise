import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import OrgProfilesService from "#/api/organization-service/org-profiles-service.api";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { OrgRenameProfileModal } from "#/components/features/settings/org-rename-profile-modal";
import { OrgDeleteProfileModal } from "#/components/features/settings/org-delete-profile-modal";
import { ProfilesBody } from "#/components/features/settings/profiles-body";
import { ProviderConnectionsManager } from "#/components/features/settings/provider-connections/provider-connections-manager";
import { LlmProfileSummary } from "#/api/settings-service/profiles-service.api";
import {
  ORG_LLM_PROFILES_QUERY_KEY,
  useOrgLlmProfiles,
} from "#/hooks/query/use-org-llm-profiles";
import { useProviderConnections } from "#/hooks/query/use-provider-connections";
import { useActivateOrgLlmProfile } from "#/hooks/mutation/use-org-llm-profile-mutations";
import { mutateWithToast } from "#/utils/mutate-with-toast";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { I18nKey } from "#/i18n/declaration";

interface OrgLlmProfilesManagerProps {
  orgId: string;
  canManage?: boolean;
  personal?: boolean;
  /**
   * Render the shared provider-connections section below the profiles. The
   * caller decides (admin permission + BYOK allowed); off by default so
   * members and managed installs never see it.
   */
  showProviderConnections?: boolean;
  onAddProfile?: () => void;
  onEditProfile?: (profile: LlmProfileSummary) => void;
}

export function OrgLlmProfilesManager({
  orgId,
  canManage = true,
  personal = false,
  showProviderConnections = false,
  onAddProfile,
  onEditProfile,
}: OrgLlmProfilesManagerProps) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useOrgLlmProfiles(orgId);
  const activateProfile = useActivateOrgLlmProfile(orgId);
  // Connection display names drive the profile grouping. Fetched for every
  // member regardless of `showProviderConnections` (the list route only needs
  // VIEW_ORG_SETTINGS), so profiles stay grouped for members who can't manage
  // the connections themselves. `ProfilesBody` keeps the flat list until the
  // names have loaded, so a raw connection id never flashes as a header.
  const { data: connections } = useProviderConnections(orgId);

  const [profileToRename, setProfileToRename] =
    useState<LlmProfileSummary | null>(null);
  const [profileToDelete, setProfileToDelete] =
    useState<LlmProfileSummary | null>(null);

  const profiles = data?.profiles ?? [];
  const client = useQueryClient();
  const selectionKey = [ORG_LLM_PROFILES_QUERY_KEY, orgId, "member-selection"];
  const selection = useQuery({
    queryKey: selectionKey,
    queryFn: () => OrgProfilesService.getSelection(orgId),
    enabled: personal,
    meta: { disableToast: true },
  });
  const select = useMutation({
    mutationFn: (name: string | null) =>
      OrgProfilesService.setSelection(orgId, name),
    onSuccess: () => client.invalidateQueries({ queryKey: selectionKey }),
    meta: { disableToast: true },
  });
  const orgDefault = data?.active_profile ?? null;
  const active = personal
    ? (selection.data?.name ??
      (selection.data?.follows_default ? orgDefault : null))
    : orgDefault;

  const connectionNamesById = useMemo(
    () =>
      Object.fromEntries(
        (connections ?? []).map((c) => [c.id, c.display_name]),
      ),
    [connections],
  );

  const handleActivate = async (name: string) => {
    await mutateWithToast(activateProfile, name, {
      success: t(I18nKey.SETTINGS$PROFILE_ACTIVATED, { name }),
      error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
    }).catch(() => null);
  };

  const handleEdit = (profile: LlmProfileSummary) => {
    onEditProfile?.(profile);
  };

  return (
    <>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-white">
            {t(I18nKey.SETTINGS$AVAILABLE_PROFILES)}
          </h2>
          {canManage && onAddProfile ? (
            <BrandButton
              testId="add-llm-profile"
              type="button"
              variant="primary"
              className="ml-auto"
              onClick={onAddProfile}
            >
              {t(I18nKey.SETTINGS$ADD_LLM_PROFILE)}
            </BrandButton>
          ) : null}
        </div>

        {personal && (profiles.length > 0 || selection.isError) && (
          <label className="flex max-w-sm flex-col gap-2 text-sm">
            {t(I18nKey.SETTINGS$PERSONAL_LLM_SELECTION)}
            <select
              className="rounded-md border border-[var(--oh-border)] bg-base-secondary p-2"
              value={
                selection.data?.name
                  ? JSON.stringify(selection.data.name)
                  : selection.data?.follows_default
                    ? "null"
                    : ""
              }
              disabled={!selection.data || select.isPending}
              onChange={(event) =>
                select.mutate(JSON.parse(event.target.value))
              }
            >
              <option value="" disabled>
                —
              </option>
              <option value="null" disabled={!orgDefault}>
                {t(I18nKey.SETTINGS$INHERIT_LLM_SELECTION, {
                  name: orgDefault ?? "—",
                })}
              </option>
              {profiles.map(({ name }) => (
                <option key={name} value={JSON.stringify(name)}>
                  {name}
                </option>
              ))}
            </select>
            {(selection.isError || select.isError) && (
              <span role="alert">
                {extractErrorMessage(
                  selection.error ?? select.error,
                  t(I18nKey.ERROR$GENERIC),
                )}
              </span>
            )}
          </label>
        )}

        <ProfilesBody
          isLoading={isLoading}
          loadError={error ?? null}
          profiles={profiles}
          active={active}
          connectionNamesById={connectionNamesById}
          onActivate={handleActivate}
          onEdit={handleEdit}
          onRename={setProfileToRename}
          onDelete={setProfileToDelete}
          isActivating={activateProfile.isPending}
          canManage={canManage}
        />
      </div>

      {showProviderConnections ? (
        <ProviderConnectionsManager orgId={orgId} profiles={profiles} />
      ) : null}

      <OrgRenameProfileModal
        orgId={orgId}
        profile={profileToRename}
        onClose={() => setProfileToRename(null)}
      />

      <OrgDeleteProfileModal
        orgId={orgId}
        profile={profileToDelete}
        onClose={() => setProfileToDelete(null)}
      />
    </>
  );
}
