import { useState } from "react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { Typography } from "#/ui/typography";
import type { MetaProfileInfo } from "#/api/organization-service/org-model-routers-service.api";
import {
  useOrgModelRouter,
  useOrgModelRouters,
} from "#/hooks/query/use-org-model-routers";
import { useOrgLlmProfiles } from "#/hooks/query/use-org-llm-profiles";
import {
  useActivateOrgModelRouter,
  useDeactivateOrgModelRouter,
  useUpdateOrgModelRouterSettings,
} from "#/hooks/mutation/use-org-model-router-mutations";
import { mutateWithToast } from "#/utils/mutate-with-toast";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { I18nKey } from "#/i18n/declaration";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";
import { ModelRouterRow } from "./model-router-row";
import { ModelRouterEditor } from "./model-router-editor";
import { DeleteModelRouterModal } from "./delete-model-router-modal";

type EditorState =
  | { mode: "list" }
  | { mode: "create" }
  | { mode: "edit"; name: string };

interface ModelRouterManagerProps {
  orgId: string;
  /** Admins/owners manage routers; members get the read-only list. */
  canManage: boolean;
}

/**
 * The org Model Router page: the router list with the Active badge, the
 * org-wide "Run on first message" switch, and (for admins) add / edit /
 * activate / deactivate / delete.
 */
export function ModelRouterManager({
  orgId,
  canManage,
}: ModelRouterManagerProps) {
  const { t } = useTranslation();
  const { data, isLoading, error } = useOrgModelRouters(orgId);
  const { data: llmProfiles } = useOrgLlmProfiles(orgId);
  const activateRouter = useActivateOrgModelRouter(orgId);
  const deactivateRouter = useDeactivateOrgModelRouter(orgId);
  const updateSettings = useUpdateOrgModelRouterSettings(orgId);

  const [editor, setEditor] = useState<EditorState>({ mode: "list" });
  const [deleteTarget, setDeleteTarget] = useState<MetaProfileInfo | null>(
    null,
  );
  const editingName = editor.mode === "edit" ? editor.name : null;
  const {
    data: editingRouter,
    isLoading: isLoadingEditingRouter,
    error: editingRouterError,
  } = useOrgModelRouter(orgId, editingName);

  const routers = data?.meta_profiles ?? [];
  const active = data?.active_meta_profile ?? null;
  const runAtStart = data?.run_at_conversation_start ?? false;
  const isBusy = activateRouter.isPending || deactivateRouter.isPending;
  const availableProfiles = (llmProfiles?.profiles ?? []).map((p) => p.name);

  const handleSetActive = async (name: string) => {
    await mutateWithToast(activateRouter, name, {
      success: t(I18nKey.SETTINGS$MODEL_ROUTER_ACTIVATED, { name }),
      error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
    }).catch(() => null);
  };

  const handleDeactivate = async (name: string) => {
    await mutateWithToast(deactivateRouter, name, {
      success: t(I18nKey.SETTINGS$MODEL_ROUTER_DEACTIVATED, { name }),
      error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
    }).catch(() => null);
  };

  const handleToggleRunAtStart = async (value: boolean) => {
    await mutateWithToast(
      updateSettings,
      { run_at_conversation_start: value },
      {
        success: false,
        error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
      },
    ).catch(() => null);
  };

  if (editor.mode === "create") {
    return (
      <ModelRouterEditor
        orgId={orgId}
        mode="create"
        existingNames={routers.map((r) => r.name)}
        availableProfiles={availableProfiles}
        onSaved={() => setEditor({ mode: "list" })}
        onCancel={() => setEditor({ mode: "list" })}
      />
    );
  }

  if (editor.mode === "edit") {
    if (editingRouterError) {
      return (
        <div className="flex flex-col gap-4">
          <Typography.Paragraph className="text-sm text-red-400">
            {t(I18nKey.SETTINGS$MODEL_ROUTER_LOAD_ERROR)}
          </Typography.Paragraph>
          <BrandButton
            type="button"
            variant="secondary"
            className="w-fit"
            onClick={() => setEditor({ mode: "list" })}
          >
            {t(I18nKey.BUTTON$CANCEL)}
          </BrandButton>
        </div>
      );
    }
    if (isLoadingEditingRouter || !editingRouter) {
      return (
        <div className="flex justify-center p-4">
          <LoadingSpinner size="large" />
        </div>
      );
    }
    return (
      <ModelRouterEditor
        key={editingRouter.name}
        orgId={orgId}
        mode="edit"
        initialName={editingRouter.name}
        initialConfig={editingRouter.config}
        existingNames={routers.map((r) => r.name)}
        availableProfiles={availableProfiles}
        onSaved={() => setEditor({ mode: "list" })}
        onCancel={() => setEditor({ mode: "list" })}
      />
    );
  }

  const renderBody = () => {
    if (isLoading) {
      return (
        <div className="flex justify-center p-4">
          <LoadingSpinner size="large" />
        </div>
      );
    }
    if (error) {
      return (
        <Typography.Paragraph className="text-sm text-red-400">
          {t(I18nKey.SETTINGS$MODEL_ROUTER_LOAD_ERROR)}
        </Typography.Paragraph>
      );
    }
    if (routers.length === 0) {
      return (
        <Typography.Paragraph className="text-sm text-[var(--oh-muted)] italic">
          {t(I18nKey.SETTINGS$MODEL_ROUTER_EMPTY)}
        </Typography.Paragraph>
      );
    }
    return (
      <div
        className={cn(
          settingsListContainerClassName,
          settingsListDividerClassName,
        )}
      >
        {routers.map((router) => (
          <ModelRouterRow
            key={router.name}
            router={router}
            isActive={router.name === active}
            canManage={canManage}
            isBusy={isBusy}
            onEdit={(name) => setEditor({ mode: "edit", name })}
            onSetActive={handleSetActive}
            onDeactivate={handleDeactivate}
            onDelete={setDeleteTarget}
          />
        ))}
      </div>
    );
  };

  return (
    <>
      <div className="flex flex-col gap-4" data-testid="model-router-manager">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-white">
            {t(I18nKey.SETTINGS$MODEL_ROUTER_AVAILABLE)}
          </h2>
          {canManage ? (
            <BrandButton
              testId="add-model-router"
              type="button"
              variant="primary"
              className="ml-auto"
              onClick={() => setEditor({ mode: "create" })}
            >
              {t(I18nKey.SETTINGS$MODEL_ROUTER_ADD)}
            </BrandButton>
          ) : null}
        </div>

        {renderBody()}

        <SettingsSwitch
          testId="model-router-run-at-start"
          isToggled={runAtStart}
          onToggle={handleToggleRunAtStart}
          isDisabled={!canManage || active === null || updateSettings.isPending}
          hint={t(I18nKey.SETTINGS$MODEL_ROUTER_RUN_AT_START_HINT)}
        >
          {t(I18nKey.SETTINGS$MODEL_ROUTER_RUN_AT_START)}
        </SettingsSwitch>
      </div>

      <DeleteModelRouterModal
        orgId={orgId}
        router={deleteTarget}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
