import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { MetaProfileInfo } from "#/api/organization-service/org-model-routers-service.api";
import { ModelRouterActionsMenu } from "./model-router-actions-menu";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import ThreeDotsVerticalIcon from "#/icons/three-dots-vertical.svg?react";
import {
  settingsListIconActionButtonClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

interface ModelRouterRowProps {
  router: MetaProfileInfo;
  isActive: boolean;
  /** Admins/owners get the actions menu; members see the row read-only. */
  canManage: boolean;
  isBusy: boolean;
  onEdit: (name: string) => void;
  onSetActive: (name: string) => void;
  onDeactivate: (name: string) => void;
  onDelete: (router: MetaProfileInfo) => void;
}

export function ModelRouterRow({
  router,
  isActive,
  canManage,
  isBusy,
  onEdit,
  onSetActive,
  onDeactivate,
  onDelete,
}: ModelRouterRowProps) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);

  return (
    <div
      data-testid="model-router-row"
      className={cn(
        settingsListRowClassName,
        "h-auto items-start justify-between gap-3 py-3 sm:items-center",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
        <div className="flex min-w-0 max-w-full items-center gap-2">
          <Typography.Text
            className="min-w-0 truncate font-medium text-white"
            title={router.name}
          >
            {router.name}
          </Typography.Text>
          {isActive && (
            <Typography.Text
              className="shrink-0 whitespace-nowrap rounded-full bg-primary px-2 py-0.5 text-xs font-semibold text-[var(--oh-color-base)]"
              testId="model-router-active-badge"
            >
              {t(I18nKey.SETTINGS$PROFILE_ACTIVE_BADGE)}
            </Typography.Text>
          )}
        </div>
        {router.classifier_model ? (
          <Typography.Text
            className="min-w-0 max-w-full truncate text-sm text-[var(--oh-muted)]"
            title={router.classifier_model}
          >
            {t(I18nKey.SETTINGS$MODEL_ROUTER_CLASSIFIER_SUMMARY, {
              model: router.classifier_model,
            })}
          </Typography.Text>
        ) : null}
      </div>
      {canManage ? (
        <div className="relative shrink-0">
          <button
            ref={triggerRef}
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={t(I18nKey.SETTINGS$MODEL_ROUTER_MENU)}
            className={settingsListIconActionButtonClassName}
            data-testid="model-router-menu-trigger"
          >
            <ThreeDotsVerticalIcon width={16} height={16} />
          </button>
          {menuOpen && (
            <ModelRouterActionsMenu
              anchorRef={triggerRef}
              onEdit={() => onEdit(router.name)}
              onSetActive={() => onSetActive(router.name)}
              onDeactivate={() => onDeactivate(router.name)}
              onDelete={() => onDelete(router)}
              isActive={isActive}
              isBusy={isBusy}
              onClose={() => setMenuOpen(false)}
            />
          )}
        </div>
      ) : null}
    </div>
  );
}
