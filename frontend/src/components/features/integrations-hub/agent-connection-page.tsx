import { useRef, useState } from "react";
import {
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  MoreVertical,
  RotateCw,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ConnectionDetailsModal } from "#/components/features/integrations-hub/connection-details-modal";
import { HubBadge } from "#/components/features/integrations-hub/hub-badge";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { IntegrationsHubPageHeader } from "#/components/features/integrations-hub/integrations-hub-page-header";
import {
  formatHubTimestamp,
  maskHubApiKey,
} from "#/components/features/integrations-hub/hub-format";
import { EditPermissionProfileModal } from "#/components/features/integrations-hub/edit-permission-profile-modal";
import { PermissionProfileActionsMenu } from "#/components/features/integrations-hub/permission-profile-actions-menu";
import { emptyPermissionProfileSnapshot } from "#/components/features/integrations-hub/permission-profile-utils";
import { SaveProfileModal } from "#/components/features/integrations-hub/save-profile-modal";
import { useIntegrationsHub } from "#/hooks/query/use-integrations-hub";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubPermissionProfile,
  HubPermissionProfileCreateInput,
  HubPermissionProfileSnapshot,
} from "#/types/integrations-hub";
import { formControlButtonClassName } from "#/utils/form-control-classes";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

function ApiKeyRow({
  testId,
  label,
  value,
}: {
  testId: string;
  label: string;
  value: string;
}) {
  const { t } = useTranslation();
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);

  return (
    <div
      data-testid={testId}
      className={cn(settingsListRowClassName, "justify-between gap-3")}
    >
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <HubTruncatedText
          text={label}
          className="text-sm font-medium text-white"
        />
        <HubTruncatedText
          text={maskHubApiKey(value, revealed)}
          className="flex-1 text-right font-mono text-xs text-tertiary-light"
        />
      </div>
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          data-testid={`${testId}-reveal`}
          aria-label={
            revealed
              ? t(I18nKey.INTEGRATIONS_HUB$HIDE)
              : t(I18nKey.INTEGRATIONS_HUB$REVEAL)
          }
          className={settingsListIconActionButtonClassName}
          onClick={() => setRevealed((current) => !current)}
        >
          {revealed ? (
            <EyeOff width={16} height={16} aria-hidden />
          ) : (
            <Eye width={16} height={16} aria-hidden />
          )}
        </button>
        <button
          type="button"
          data-testid={`${testId}-copy`}
          aria-label={t(I18nKey.INTEGRATIONS_HUB$COPY)}
          className={settingsListIconActionButtonClassName}
          onClick={async () => {
            await navigator.clipboard?.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? (
            <CheckCircle2 width={16} height={16} aria-hidden />
          ) : (
            <Copy width={16} height={16} aria-hidden />
          )}
        </button>
        <button
          type="button"
          data-testid={`${testId}-rotate`}
          aria-label={t(I18nKey.INTEGRATIONS_HUB$ROTATE)}
          className={settingsListIconActionButtonClassName}
        >
          <RotateCw width={16} height={16} aria-hidden />
        </button>
      </div>
    </div>
  );
}

function ProfileAgentKey({
  profileId,
  value,
}: {
  profileId: string;
  value: string;
}) {
  const { t } = useTranslation();
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);
  const testId = `agent-connection-agent-key-row-${profileId}`;

  return (
    <div className="flex min-w-0 items-center gap-2">
      <HubTruncatedText
        text={maskHubApiKey(value, revealed)}
        className="max-w-48 text-right font-mono text-xs text-tertiary-light"
      />
      <div className="flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          data-testid={`${testId}-reveal`}
          aria-label={
            revealed
              ? t(I18nKey.INTEGRATIONS_HUB$HIDE)
              : t(I18nKey.INTEGRATIONS_HUB$REVEAL)
          }
          className={settingsListIconActionButtonClassName}
          onClick={() => setRevealed((current) => !current)}
        >
          {revealed ? (
            <EyeOff width={16} height={16} aria-hidden />
          ) : (
            <Eye width={16} height={16} aria-hidden />
          )}
        </button>
        <button
          type="button"
          data-testid={`${testId}-copy`}
          aria-label={t(I18nKey.INTEGRATIONS_HUB$COPY)}
          className={settingsListIconActionButtonClassName}
          onClick={async () => {
            await navigator.clipboard?.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? (
            <CheckCircle2 width={16} height={16} aria-hidden />
          ) : (
            <Copy width={16} height={16} aria-hidden />
          )}
        </button>
        <button
          type="button"
          data-testid={`${testId}-rotate`}
          aria-label={t(I18nKey.INTEGRATIONS_HUB$ROTATE)}
          className={settingsListIconActionButtonClassName}
        >
          <RotateCw width={16} height={16} aria-hidden />
        </button>
      </div>
    </div>
  );
}

function PermissionProfileMenuButton({
  profile,
  isOpen,
  onToggle,
  onClose,
  onEditPermissions,
  onDuplicate,
  onSetDefault,
  onDelete,
}: {
  profile: HubPermissionProfile;
  isOpen: boolean;
  onToggle: () => void;
  onClose: () => void;
  onEditPermissions: () => void;
  onDuplicate: () => void;
  onSetDefault: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const triggerRef = useRef<HTMLButtonElement>(null);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        data-testid={`permission-profile-menu-trigger-${profile.id}`}
        aria-label={t(I18nKey.INTEGRATIONS_HUB$PROFILE_ACTIONS, {
          name: profile.name,
        })}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        onClick={onToggle}
        className={settingsListIconActionButtonClassName}
      >
        <MoreVertical aria-hidden className="size-4" strokeWidth={2} />
      </button>
      {isOpen ? (
        <PermissionProfileActionsMenu
          profileName={profile.name}
          isDefault={profile.isDefault}
          isDeleteDisabled={profile.isDefault}
          anchorRef={triggerRef}
          onClose={onClose}
          onEditPermissions={onEditPermissions}
          onDuplicate={onDuplicate}
          onLoadProfile={onClose}
          onUpdateFromCurrent={onClose}
          onSetDefault={onSetDefault}
          onDelete={onDelete}
        />
      ) : null}
    </>
  );
}

export function AgentConnectionPage() {
  const { t } = useTranslation();
  const {
    apiKeys,
    integrations,
    permissionProfiles,
    isPersonalWorkspace,
    addPermissionProfile,
    savePermissionProfileSnapshot,
    setDefaultPermissionProfile,
    deletePermissionProfile,
  } = useIntegrationsHub();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [createDraftName, setCreateDraftName] = useState<string | null>(null);
  const [editingProfileId, setEditingProfileId] = useState<string | null>(null);
  const [menuProfileId, setMenuProfileId] = useState<string | null>(null);
  const createDraftProfile: HubPermissionProfile | null = createDraftName
    ? {
        id: "draft-profile",
        name: createDraftName,
        summary: "0 tools enabled",
        updatedAt: "",
        agentApiKey: "",
        snapshot: emptyPermissionProfileSnapshot(),
      }
    : null;
  const editingProfile =
    createDraftProfile ??
    permissionProfiles.find((profile) => profile.id === editingProfileId) ??
    null;

  const handleCreateProfile = (input: HubPermissionProfileCreateInput) => {
    if (input.source === "scratch") {
      setSaveOpen(false);
      setCreateDraftName(input.name);
      return;
    }
    const source = permissionProfiles.find(
      (profile) => profile.id === input.sourceProfileId,
    );
    addPermissionProfile(input.name, source?.snapshot);
    setSaveOpen(false);
  };

  const handleSaveEditor = (snapshot: HubPermissionProfileSnapshot) => {
    if (createDraftName) {
      addPermissionProfile(createDraftName, snapshot);
      setCreateDraftName(null);
      return;
    }
    if (editingProfileId) {
      savePermissionProfileSnapshot(editingProfileId, snapshot);
      setEditingProfileId(null);
    }
  };
  const visibleKeys = isPersonalWorkspace
    ? apiKeys.filter((key) => key.id !== "key-2")
    : apiKeys;

  return (
    <div
      className="flex flex-col gap-6"
      data-testid="integrations-hub-connection"
    >
      <IntegrationsHubPageHeader
        title={I18nKey.INTEGRATIONS_HUB$NAV_AGENT_CONNECTION}
        subtitle={I18nKey.INTEGRATIONS_HUB$PAGE_AGENT_CONNECTION_SUBLINE}
        subtitleExtra={
          <>
            {" "}
            <button
              type="button"
              data-testid="agent-connection-details-link"
              onClick={() => setDetailsOpen(true)}
              className="cursor-pointer border-0 bg-transparent p-0 text-sm text-white underline-offset-2 hover:underline"
            >
              {t(I18nKey.INTEGRATIONS_HUB$CONNECTION_DETAILS)}
            </button>
          </>
        }
      />

      <section
        data-testid="agent-connection-profile-keys"
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-base font-medium text-white">
              {t(I18nKey.INTEGRATIONS_HUB$CONNECTION_PROFILES)}
            </h2>
            <button
              type="button"
              data-testid="agent-connection-new-profile"
              onClick={() => setSaveOpen(true)}
              className={cn(
                formControlButtonClassName,
                "shrink-0 border border-[var(--oh-border)] bg-base-secondary text-white hover:bg-surface-raised",
              )}
            >
              {t(I18nKey.INTEGRATIONS_HUB$NEW_PROFILE)}
            </button>
          </div>
          <p className="text-sm leading-5 text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$PROFILES_DESC)}
          </p>
        </div>
        <div
          data-testid="saved-permission-profiles-section"
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
          )}
        >
          {permissionProfiles.map((profile) => {
            const updated = formatHubTimestamp(profile.updatedAt);
            return (
              <div
                key={profile.id}
                data-testid={`permission-profile-row-${profile.id}`}
                className={cn(
                  settingsListRowClassName,
                  "h-auto min-h-12 justify-between gap-3 py-3",
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <HubTruncatedText
                      text={profile.name}
                      className="text-sm font-medium text-white"
                    />
                    {profile.isDefault ? (
                      <HubBadge tone="success" size="sm">
                        {t(I18nKey.INTEGRATIONS_HUB$DEFAULT)}
                      </HubBadge>
                    ) : null}
                    {updated ? (
                      <span className="text-xs text-[var(--oh-text-dim)]">
                        {t(I18nKey.INTEGRATIONS_HUB$UPDATED, {
                          date: updated.date,
                        })}
                      </span>
                    ) : null}
                  </div>
                  <HubTruncatedText
                    text={profile.summary}
                    className="mt-1 text-xs text-[var(--oh-text-dim)]"
                  />
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <ProfileAgentKey
                    profileId={profile.id}
                    value={profile.agentApiKey}
                  />
                  <PermissionProfileMenuButton
                    profile={profile}
                    isOpen={menuProfileId === profile.id}
                    onToggle={() =>
                      setMenuProfileId((current) =>
                        current === profile.id ? null : profile.id,
                      )
                    }
                    onClose={() => setMenuProfileId(null)}
                    onEditPermissions={() => setEditingProfileId(profile.id)}
                    onDuplicate={() =>
                      addPermissionProfile(
                        `Copy of ${profile.name}`,
                        profile.snapshot,
                      )
                    }
                    onSetDefault={() => setDefaultPermissionProfile(profile.id)}
                    onDelete={() => deletePermissionProfile(profile.id)}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section
        data-testid="agent-connection-automation-keys"
        className="flex flex-col gap-4 border-t border-[var(--oh-border)] pt-6"
      >
        <div className="flex flex-col gap-1">
          <h2 className="text-base font-medium text-white">
            {t(I18nKey.INTEGRATIONS_HUB$CONNECTION_KEYS)}
          </h2>
          <p className="text-sm leading-5 text-tertiary-light">
            {t(I18nKey.INTEGRATIONS_HUB$API_KEYS_DESC)}
          </p>
        </div>
        <div
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
          )}
        >
          {visibleKeys.map((key) => (
            <ApiKeyRow
              key={key.id}
              testId={`integrations-hub-key-${key.id}`}
              label={key.name}
              value={key.value}
            />
          ))}
        </div>
      </section>

      {detailsOpen ? (
        <ConnectionDetailsModal
          isAdmin={!isPersonalWorkspace}
          onClose={() => setDetailsOpen(false)}
        />
      ) : null}

      {saveOpen ? (
        <SaveProfileModal
          profiles={permissionProfiles}
          onClose={() => setSaveOpen(false)}
          onSave={handleCreateProfile}
        />
      ) : null}

      {editingProfile ? (
        <EditPermissionProfileModal
          profile={editingProfile}
          integrations={integrations}
          onClose={() => {
            setCreateDraftName(null);
            setEditingProfileId(null);
          }}
          onSave={handleSaveEditor}
        />
      ) : null}
    </div>
  );
}
