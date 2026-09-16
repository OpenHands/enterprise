import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { IntegrationProviderIcon } from "#/components/features/settings/git-settings/integration-provider-icon";
import { HubBadge } from "#/components/features/integrations-hub/hub-badge";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";
import { HubSearchField } from "#/components/features/integrations-hub/hub-search-field";
import { AccessModeDropdown } from "#/components/features/integrations-hub/integration-detail-modal";
import { HubModalCloseButton } from "#/components/features/integrations-hub/hub-modal";
import {
  buildPermissionProfileEditorRows,
  editorRowsToSnapshot,
  formatPermissionProfileSummary,
  matchesPermissionProfileEditorSearch,
  matchesPermissionProfileToolSearch,
  permissionProfileSnapshotsEqual,
  profileSnapshotOrEmpty,
  type PermissionProfileEditorIntegration,
} from "#/components/features/integrations-hub/permission-profile-utils";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { I18nKey } from "#/i18n/declaration";
import type {
  HubIntegration,
  HubPermissionProfile,
  HubPermissionProfileSnapshot,
  HubToolAccessMode,
} from "#/types/integrations-hub";
import { cn } from "#/utils/utils";

interface EditPermissionProfileModalProps {
  profile: HubPermissionProfile;
  integrations: HubIntegration[];
  onClose: () => void;
  onSave: (snapshot: HubPermissionProfileSnapshot) => void;
}

export function EditPermissionProfileModal({
  profile,
  integrations,
  onClose,
  onSave,
}: EditPermissionProfileModalProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState(() =>
    buildPermissionProfileEditorRows(
      profileSnapshotOrEmpty(profile),
      integrations,
    ),
  );
  const [search, setSearch] = useState("");
  const [toolSearch, setToolSearch] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const initialSnapshot = useMemo(
    () =>
      editorRowsToSnapshot(
        buildPermissionProfileEditorRows(
          profileSnapshotOrEmpty(profile),
          integrations,
        ),
      ),
    [integrations, profile],
  );

  const visibleRows = rows.filter((row) =>
    matchesPermissionProfileEditorSearch(row, search),
  );
  const selectedRow = selectedKey
    ? (rows.find((row) => row.key === selectedKey) ?? null)
    : null;
  const visibleTools = selectedRow
    ? selectedRow.tools.filter((tool) =>
        matchesPermissionProfileToolSearch(tool, toolSearch),
      )
    : [];
  const draftSnapshot = editorRowsToSnapshot(rows);
  const isDirty = !permissionProfileSnapshotsEqual(
    draftSnapshot,
    initialSnapshot,
  );
  const summary = formatPermissionProfileSummary(draftSnapshot, integrations);
  const isToolsPage = selectedRow !== null;

  const updateRow = (
    key: string,
    updater: (
      row: PermissionProfileEditorIntegration,
    ) => PermissionProfileEditorIntegration,
  ) => {
    setRows((current) =>
      current.map((row) => (row.key === key ? updater(row) : row)),
    );
  };

  const handleToggleEnabled = (key: string, enabled: boolean) => {
    updateRow(key, (row) => ({ ...row, enabled }));
  };

  const handleToolAccessMode = (
    integrationKey: string,
    toolName: string,
    accessMode: HubToolAccessMode,
  ) => {
    updateRow(integrationKey, (row) => ({
      ...row,
      tools: row.tools.map((tool) =>
        tool.name === toolName ? { ...tool, accessMode } : tool,
      ),
    }));
  };

  const openToolsPage = (key: string) => {
    setSelectedKey(key);
    setToolSearch("");
  };

  const closeToolsPage = () => {
    setSelectedKey(null);
    setToolSearch("");
  };

  let integrationListContent: ReactNode;
  if (rows.length === 0) {
    integrationListContent = (
      <p className="px-4 py-4 text-sm text-[var(--oh-text-secondary)]">
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_EMPTY)}
      </p>
    );
  } else if (visibleRows.length === 0) {
    integrationListContent = (
      <p className="px-4 py-4 text-sm text-[var(--oh-text-secondary)]">
        {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_NO_MATCHES)}
      </p>
    );
  } else {
    integrationListContent = (
      <div className="divide-y divide-[var(--oh-border)]">
        {visibleRows.map((row) => (
          <div
            key={row.key}
            data-testid={`edit-permission-profile-integration-${row.key}`}
            className="flex items-center gap-3 px-4 py-3"
          >
            <IntegrationProviderIcon provider={row.key} size="sm" />
            <button
              type="button"
              className="min-w-0 flex-1 cursor-pointer text-left"
              aria-label={t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_VIEW_TOOLS, {
                name: row.name,
              })}
              onClick={() => openToolsPage(row.key)}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <HubTruncatedText
                  text={row.name}
                  className="text-sm font-medium text-white"
                />
                {row.installed ? null : (
                  <HubBadge size="sm">
                    {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_NOT_INSTALLED)}
                  </HubBadge>
                )}
              </div>
              <p className="mt-0.5 text-xs text-[var(--oh-text-dim)]">
                {t(I18nKey.INTEGRATIONS_HUB$TOOLS_COUNT, {
                  count: row.tools.length,
                })}
              </p>
            </button>
            <SettingsSwitch
              testId={`edit-permission-profile-toggle-${row.key}`}
              isToggled={row.enabled}
              onToggle={(enabled) => handleToggleEnabled(row.key, enabled)}
            />
            <button
              type="button"
              aria-label={t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_VIEW_TOOLS, {
                name: row.name,
              })}
              onClick={() => openToolsPage(row.key)}
              className="inline-flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-tertiary-alt transition-colors hover:bg-[var(--oh-interactive-active)] hover:text-white"
            >
              <ChevronRight aria-hidden className="h-4 w-4 shrink-0" />
            </button>
          </div>
        ))}
      </div>
    );
  }

  let toolListContent: ReactNode = null;
  if (selectedRow) {
    if (selectedRow.tools.length === 0) {
      toolListContent = (
        <p className="px-4 py-4 text-sm text-[var(--oh-text-secondary)]">
          {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_NO_TOOLS)}
        </p>
      );
    } else if (visibleTools.length === 0) {
      toolListContent = (
        <p className="px-4 py-4 text-sm text-[var(--oh-text-secondary)]">
          {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_NO_TOOL_MATCHES)}
        </p>
      );
    } else {
      toolListContent = (
        <div className="divide-y divide-[var(--oh-border)]">
          {visibleTools.map((tool) => (
            <div
              key={tool.name}
              className="flex items-center justify-between gap-3 px-3 py-2"
              data-testid={`edit-permission-profile-tool-${selectedRow.key}-${tool.name}`}
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium leading-5 text-white">
                  {tool.name}
                </p>
                <HubTruncatedText
                  text={
                    tool.missing
                      ? t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_MISSING_TOOL)
                      : tool.description ||
                        t(I18nKey.INTEGRATIONS_HUB$TOOL_NO_DESCRIPTION)
                  }
                  className="mt-0.5 text-xs leading-4 text-[var(--oh-text-secondary)]"
                />
              </div>
              <AccessModeDropdown
                toolName={`${selectedRow.key}-${tool.name}`}
                mode={tool.accessMode}
                onChange={(nextMode) =>
                  handleToolAccessMode(selectedRow.key, tool.name, nextMode)
                }
              />
            </div>
          ))}
        </div>
      );
    }
  }

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") {
        return;
      }
      if (selectedKey) {
        event.stopPropagation();
        setSelectedKey(null);
        setToolSearch("");
        return;
      }
      onClose();
    };

    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose, selectedKey]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isDirty) {
      return;
    }
    onSave(draftSnapshot);
    onClose();
  };

  return (
    <ModalBackdrop
      aria-label={t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_ARIA)}
      closeOnEscape={false}
      onClose={onClose}
    >
      <form
        data-testid="edit-permission-profile-modal"
        data-profile-id={profile.id}
        data-page={isToolsPage ? "tools" : "list"}
        onSubmit={handleSubmit}
        className={cn(
          "relative flex min-h-[32rem] max-h-[90vh] w-[720px] max-w-[90vw] flex-col overflow-hidden",
          "rounded-2xl border border-white/10 bg-base-secondary shadow-2xl",
        )}
      >
        <HubModalCloseButton
          onClose={onClose}
          testId="edit-permission-profile-modal-close"
        />

        <div className="relative min-h-0 flex-1 overflow-hidden">
          <div
            className={cn(
              "flex h-full w-[200%] transition-transform duration-300 ease-out motion-reduce:transition-none",
              isToolsPage ? "-translate-x-1/2" : "translate-x-0",
            )}
          >
            <div
              className="flex h-full w-1/2 shrink-0 flex-col"
              aria-hidden={isToolsPage}
              data-testid="edit-permission-profile-list-pane"
            >
              <div className="custom-scrollbar-always min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-7">
                <div className="pr-6">
                  <h2
                    data-testid="edit-permission-profile-modal-title"
                    className="text-base font-semibold text-white"
                  >
                    {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_TITLE, {
                      name: profile.name,
                    })}
                  </h2>
                  <p className="mt-1 text-sm leading-5 text-tertiary-light">
                    {profile.isDefault
                      ? t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_BODY_DEFAULT)
                      : t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_BODY)}
                  </p>
                  <p
                    data-testid="edit-permission-profile-modal-summary"
                    className="mt-2 text-xs text-[var(--oh-text-dim)]"
                  >
                    {summary}
                  </p>
                </div>

                <div className="mt-4">
                  <HubSearchField
                    value={search}
                    onChange={setSearch}
                    placeholder={t(
                      I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_SEARCH,
                    )}
                    testId="edit-permission-profile-search"
                  />
                </div>

                <div className="mt-4 overflow-hidden rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)]">
                  {integrationListContent}
                </div>
              </div>
            </div>

            <div
              className="flex h-full w-1/2 shrink-0 flex-col"
              aria-hidden={!isToolsPage}
              data-testid="edit-permission-profile-tools-pane"
            >
              <div className="custom-scrollbar-always min-h-0 flex-1 overflow-y-auto px-7 pb-4 pt-7">
                <button
                  type="button"
                  data-testid="edit-permission-profile-tools-back"
                  onClick={closeToolsPage}
                  className="mb-4 inline-flex cursor-pointer items-center gap-1 text-sm text-tertiary-light transition-colors hover:text-white"
                >
                  <ChevronLeft aria-hidden className="h-4 w-4" />
                  {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_BACK)}
                </button>

                {selectedRow ? (
                  <>
                    <div className="flex items-start gap-3 pr-6">
                      <IntegrationProviderIcon
                        provider={selectedRow.key}
                        size="md"
                      />
                      <div className="min-w-0 flex-1">
                        <h2 className="min-w-0 text-base font-semibold text-white">
                          <HubTruncatedText text={selectedRow.name} />
                        </h2>
                        <p className="mt-0.5 text-xs text-[var(--oh-text-dim)]">
                          {t(I18nKey.INTEGRATIONS_HUB$TOOLS_COUNT, {
                            count: selectedRow.tools.length,
                          })}
                        </p>
                      </div>
                      <SettingsSwitch
                        testId={`edit-permission-profile-tools-toggle-${selectedRow.key}`}
                        isToggled={selectedRow.enabled}
                        onToggle={(enabled) =>
                          handleToggleEnabled(selectedRow.key, enabled)
                        }
                      />
                    </div>

                    <div className="mt-4">
                      <HubSearchField
                        value={toolSearch}
                        onChange={setToolSearch}
                        placeholder={t(
                          I18nKey.INTEGRATIONS_HUB$WIZARD_SEARCH_TOOLS,
                        )}
                        testId="edit-permission-profile-tool-search"
                      />
                    </div>

                    <div className="mt-4 overflow-hidden rounded-xl border border-[var(--oh-border)] bg-[var(--oh-surface-subtle)]">
                      {toolListContent}
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-[var(--oh-border)] px-7 py-4">
          <BrandButton type="button" variant="secondary" onClick={onClose}>
            {t(I18nKey.BUTTON$CANCEL)}
          </BrandButton>
          <BrandButton type="submit" variant="primary" isDisabled={!isDirty}>
            {t(I18nKey.INTEGRATIONS_HUB$PROFILE_EDIT_SAVE)}
          </BrandButton>
        </div>
      </form>
    </ModalBackdrop>
  );
}
