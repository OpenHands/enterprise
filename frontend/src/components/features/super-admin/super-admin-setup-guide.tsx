import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { Check, ClipboardList } from "lucide-react";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import {
  SIDEBAR_ICON_SLOT_CLASS,
  SIDEBAR_ROW_INTERACTIVE_CLASS,
  navInteractiveTransitionClassName,
} from "#/components/features/sidebar/sidebar-layout";
import { BrandButton } from "./super-admin-chrome";
import {
  SUPER_ADMIN_SETUP_STEPS,
  useSuperAdminSetup,
  setSuperAdminSetupStepComplete,
  setSuperAdminSetupVisible,
  type SuperAdminSetupStep,
} from "./super-admin-setup";

function SetupProgressBar({
  progress,
  className,
}: {
  progress: number;
  className?: string;
}) {
  const width = `${Math.round(Math.min(1, Math.max(0, progress)) * 100)}%`;
  return (
    <div
      className={cn(
        "h-1 w-full overflow-hidden rounded-full bg-[var(--oh-border)]",
        className,
      )}
      aria-hidden
    >
      <div
        className="h-full rounded-full bg-foreground transition-[width] duration-300"
        style={{ width }}
      />
    </div>
  );
}

export function SuperAdminSetupGuideWidget({
  onNavigate,
}: {
  onNavigate?: () => void;
}) {
  const { t } = useTranslation();
  const { nextStep, progress } = useSuperAdminSetup();
  const nextLabel = nextStep
    ? t(I18nKey.SUPER_ADMIN$SETUP_NEXT, {
        step: t(nextStep.title as I18nKey),
      })
    : t(I18nKey.SUPER_ADMIN$SETUP_COMPLETE);

  return (
    <NavLink
      to={SUPER_ADMIN_PATHS.setup}
      onClick={onNavigate}
      data-testid="super-admin-setup-widget"
      aria-label={t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
      className={({ isActive }) =>
        cn(
          "flex w-full min-w-0 flex-col gap-2 rounded-md px-2.5 py-2.5",
          navInteractiveTransitionClassName,
          isActive
            ? SIDEBAR_ROW_INTERACTIVE_CLASS.active
            : SIDEBAR_ROW_INTERACTIVE_CLASS.idle,
        )
      }
    >
      <div className="flex min-w-0 items-start gap-2">
        <span className={cn(SIDEBAR_ICON_SLOT_CLASS, "h-5 min-h-5")}>
          <ClipboardList className="size-4" strokeWidth={2} aria-hidden />
        </span>
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="truncate text-sm font-medium leading-5">
            {t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
          </span>
          <span className="truncate text-xs leading-4 text-[var(--oh-muted)]">
            {nextLabel}
          </span>
          <SetupProgressBar progress={progress} className="mt-1.5" />
        </div>
      </div>
    </NavLink>
  );
}

export function SuperAdminSetupNav({
  onNavigate,
  className,
}: {
  onNavigate?: () => void;
  className?: string;
}) {
  const { visible } = useSuperAdminSetup();
  if (!visible) {
    return null;
  }

  return (
    <div className={cn("mb-3", className)}>
      <div
        className={settingsListContainerClassName}
        data-testid="super-admin-setup-container"
      >
        <SuperAdminSetupGuideWidget onNavigate={onNavigate} />
      </div>
    </div>
  );
}

function SetupStepRow({
  step,
  complete,
}: {
  step: SuperAdminSetupStep;
  complete: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div
      data-testid={`super-admin-setup-step-${step.id}`}
      className="flex items-start gap-3 px-3 py-3"
    >
      <button
        type="button"
        data-testid={`super-admin-setup-toggle-${step.id}`}
        aria-pressed={complete}
        aria-label={
          complete
            ? t(I18nKey.SUPER_ADMIN$SETUP_MARK_INCOMPLETE)
            : t(I18nKey.SUPER_ADMIN$SETUP_MARK_COMPLETE)
        }
        onClick={() => setSuperAdminSetupStepComplete(step.id, !complete)}
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
          complete
            ? "border-foreground bg-foreground text-base"
            : "border-[var(--oh-border)] bg-transparent text-transparent",
        )}
      >
        <Check className="size-3" strokeWidth={3} aria-hidden />
      </button>
      <div className="min-w-0 flex-1 space-y-1">
        <p
          className={cn(
            "text-sm font-medium leading-5",
            complete ? "text-[var(--oh-muted)] line-through" : "text-white",
          )}
        >
          {t(step.title as I18nKey)}
        </p>
        <p className="text-sm leading-5 text-[var(--oh-muted)]">
          {t(step.description as I18nKey)}
        </p>
      </div>
      <BrandButton
        type="button"
        variant="secondary"
        className="shrink-0"
        onClick={() => navigate(step.to)}
      >
        {t(I18nKey.SUPER_ADMIN$SETUP_OPEN)}
      </BrandButton>
    </div>
  );
}

export function SuperAdminSetupGuide() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { completed, nextStep, completedCount, totalCount, progress, visible } =
    useSuperAdminSetup();
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeBecauseComplete, setRemoveBecauseComplete] = useState(false);
  const wasComplete = useRef(progress === 1);

  useEffect(() => {
    if (progress === 1 && !wasComplete.current && visible) {
      setRemoveBecauseComplete(true);
      setRemoveOpen(true);
    }
    wasComplete.current = progress === 1;
  }, [progress, visible]);

  const hideGuide = () => {
    setRemoveOpen(false);
    setSuperAdminSetupVisible(false);
    navigate(SUPER_ADMIN_PATHS.root);
  };

  return (
    <div className="flex flex-col gap-4" data-testid="super-admin-setup">
      <p className="text-sm text-[var(--oh-muted)]">
        {nextStep
          ? t(I18nKey.SUPER_ADMIN$SETUP_NEXT, {
              step: t(nextStep.title as I18nKey),
            })
          : t(I18nKey.SUPER_ADMIN$SETUP_COMPLETE)}
      </p>
      <div className="flex items-center gap-3">
        <SetupProgressBar progress={progress} />
        <span className="shrink-0 text-xs tabular-nums text-[var(--oh-muted)]">
          {completedCount}/{totalCount}
        </span>
      </div>
      <div
        className={cn(
          settingsListContainerClassName,
          settingsListDividerClassName,
        )}
      >
        {SUPER_ADMIN_SETUP_STEPS.map((step) => (
          <SetupStepRow
            key={step.id}
            step={step}
            complete={completed.has(step.id)}
          />
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between gap-4 border-t border-[var(--oh-border)] pt-6">
        <p className="min-w-0 text-sm leading-5 text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$SETUP_REMOVE_HINT)}
        </p>
        <BrandButton
          type="button"
          variant="secondary"
          testId="super-admin-setup-remove"
          className="shrink-0"
          onClick={() => {
            setRemoveBecauseComplete(false);
            setRemoveOpen(true);
          }}
        >
          {t(I18nKey.SUPER_ADMIN$SETUP_REMOVE)}
        </BrandButton>
      </div>
      {removeOpen && (
        <ConfirmationModal
          text={
            removeBecauseComplete
              ? t(I18nKey.SUPER_ADMIN$SETUP_REMOVE_COMPLETE_CONFIRM)
              : t(I18nKey.SUPER_ADMIN$SETUP_REMOVE_CONFIRM)
          }
          onConfirm={hideGuide}
          onCancel={() => setRemoveOpen(false)}
        />
      )}
    </div>
  );
}
