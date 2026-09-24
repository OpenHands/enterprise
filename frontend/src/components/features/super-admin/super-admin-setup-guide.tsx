import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  ArrowUpRight,
  Check,
  ChevronRight,
  ClipboardList,
  X,
} from "lucide-react";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { I18nKey } from "#/i18n/declaration";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";
import { useConfig } from "#/hooks/query/use-config";
import { useMe } from "#/hooks/query/use-me";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import {
  getSuperAdminNuxStep,
  readSuperAdminNux,
  subscribeSuperAdminNux,
} from "#/utils/org/super-admin-nux";
import {
  getSetupTestSuperAdminAccessOverride,
  readSetupTestPersona,
  subscribeSetupTestPersona,
} from "#/utils/org/setup-test-harness";
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
  type SuperAdminSetupStepId,
} from "./super-admin-setup";
import { SUPER_ADMIN_SETUP_TOUR } from "#/components/features/setup/tours/super-admin-setup-tour";
import {
  startGuidedTour,
  stopGuidedTour,
  isGuidedTourActive,
  subscribeGuidedTourActive,
} from "#/components/features/setup/tours/tour-engine";

async function runSuperAdminSetupTour(
  navigate: (to: string) => void,
  startAtStepId?: string,
): Promise<void> {
  // Checklist completion is driven by real actions (e.g. org created),
  // not by walking through spotlight tips.
  await startGuidedTour(SUPER_ADMIN_SETUP_TOUR, navigate, {
    startAtStepId,
  });
}

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

/**
 * Floating lower-right control for Super Admin setup tasks (demo-tour style).
 * Shown while the setup guide is still visible after install NUX.
 */
function FloatingSetupStepList({
  onOpenStep,
  onStartTour,
  tourStarting,
}: {
  onOpenStep: (step: SuperAdminSetupStep) => void;
  onStartTour: (stepId: SuperAdminSetupStepId) => void | Promise<void>;
  tourStarting: boolean;
}) {
  const { t } = useTranslation();
  const { completed, nextStep } = useSuperAdminSetup();

  return (
    <ul className="flex flex-col gap-0.5">
      {SUPER_ADMIN_SETUP_STEPS.map((step) => {
        const done = completed.has(step.id);
        const selected = nextStep?.id === step.id;
        return (
          <li key={step.id}>
            <div
              className={cn(
                "group flex w-full items-center gap-1 rounded-lg px-1.5 py-1",
                "hover:bg-base",
                selected && "bg-base",
              )}
              data-testid={`super-admin-setup-floating-step-${step.id}`}
            >
              <button
                type="button"
                className="flex min-w-0 flex-1 items-center gap-2.5 rounded-md px-1 py-1 text-left"
                onClick={() => onOpenStep(step)}
              >
                <span
                  className={cn(
                    "flex size-4 shrink-0 items-center justify-center rounded-full",
                    done
                      ? "bg-foreground text-base"
                      : "border border-[var(--oh-border)] bg-transparent",
                  )}
                  aria-hidden
                >
                  {done && <Check className="size-2.5" strokeWidth={3} />}
                </span>
                <span
                  className={cn(
                    "text-xs leading-4",
                    done ? "text-[var(--oh-muted)] line-through" : "text-white",
                  )}
                >
                  {t(step.title as I18nKey)}
                </span>
              </button>
              <button
                type="button"
                data-testid={`super-admin-setup-floating-tour-${step.id}`}
                aria-label={t(I18nKey.SUPER_ADMIN$SETUP_START_GUIDE)}
                title={t(I18nKey.SUPER_ADMIN$SETUP_START_GUIDE)}
                disabled={tourStarting}
                className={cn(
                  "shrink-0 rounded p-1 text-[var(--oh-muted)]",
                  "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
                  "hover:text-white",
                  "disabled:opacity-50",
                )}
                onClick={() => onStartTour(step.id)}
              >
                <ChevronRight className="size-4" strokeWidth={2} aria-hidden />
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function SuperAdminSetupFloatingWidget() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { data: me } = useMe();
  const { data: config } = useConfig();
  const { visible, nextStep, progress, completedCount, totalCount } =
    useSuperAdminSetup();
  const nux = useSyncExternalStore(
    subscribeSuperAdminNux,
    readSuperAdminNux,
    readSuperAdminNux,
  );
  const [open, setOpen] = useState(true);
  const [tourStarting, setTourStarting] = useState(false);
  const tourActive = useSyncExternalStore(
    subscribeGuidedTourActive,
    isGuidedTourActive,
    isGuidedTourActive,
  );
  // Re-render when mock persona override changes SA widget visibility.
  useSyncExternalStore(
    subscribeSetupTestPersona,
    readSetupTestPersona,
    () => "live",
  );

  const nuxDone = getSuperAdminNuxStep(nux) === "done";
  const isInstallRoute = pathname.startsWith("/install");
  const saAccessOverride = getSetupTestSuperAdminAccessOverride();
  const canAccess =
    saAccessOverride === null
      ? canAccessSuperAdminDashboard(config?.feature_flags, me?.permissions)
      : saAccessOverride;

  useEffect(() => {
    if (tourActive) {
      setOpen(false);
    }
  }, [tourActive]);

  const startTour = async (fromStepId?: string) => {
    setTourStarting(true);
    setOpen(false);
    try {
      await runSuperAdminSetupTour(
        navigate,
        fromStepId ?? nextStep?.id ?? SUPER_ADMIN_SETUP_STEPS[0]?.id,
      );
    } finally {
      setTourStarting(false);
    }
  };

  if (!canAccess || !visible || !nuxDone || isInstallRoute || tourActive) {
    return null;
  }

  const nextLabel = nextStep
    ? t(I18nKey.SUPER_ADMIN$SETUP_NEXT, {
        step: t(nextStep.title as I18nKey),
      })
    : t(I18nKey.SUPER_ADMIN$SETUP_COMPLETE);
  const progressPct = Math.round(Math.min(1, Math.max(0, progress)) * 100);

  return (
    <div
      className="fixed bottom-5 right-5 z-[65] flex flex-col items-end gap-2"
      data-testid="super-admin-setup-floating"
    >
      {open && (
        <div
          data-testid="super-admin-setup-floating-panel"
          className={cn(
            "flex w-[320px] flex-col gap-3 rounded-xl border border-[var(--oh-border)]",
            "bg-base-secondary p-4 shadow-lg",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <p className="min-w-0 text-sm font-semibold leading-7 text-white">
              {t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
            </p>
            <div className="flex h-7 shrink-0 items-center gap-0.5">
              <button
                type="button"
                data-testid="super-admin-setup-floating-page"
                aria-label={t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
                title={t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
                className={cn(
                  "inline-flex h-7 w-7 shrink-0 items-center justify-center",
                  "rounded-md border-0 bg-transparent p-0",
                  "text-[var(--oh-muted)] hover:bg-base hover:text-white",
                )}
                onClick={() => {
                  stopGuidedTour();
                  navigate(SUPER_ADMIN_PATHS.setup);
                }}
              >
                <ArrowUpRight
                  className="size-4 shrink-0"
                  strokeWidth={2}
                  aria-hidden
                />
              </button>
              <button
                type="button"
                aria-label={t(I18nKey.BUTTON$CLOSE)}
                className={cn(
                  "inline-flex h-7 w-7 shrink-0 items-center justify-center",
                  "rounded-md border-0 bg-transparent p-0",
                  "text-[var(--oh-muted)] hover:bg-base hover:text-white",
                )}
                onClick={() => setOpen(false)}
              >
                <X className="size-4 shrink-0" strokeWidth={2} aria-hidden />
              </button>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div
              className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--oh-border)]"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progressPct}
              aria-label={t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
            >
              <div
                className="h-full rounded-full bg-foreground transition-[width] duration-300"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <span className="shrink-0 text-xs tabular-nums text-[var(--oh-muted)]">
              {completedCount}/{totalCount}
            </span>
          </div>

          <FloatingSetupStepList
            tourStarting={tourStarting}
            onOpenStep={(step) => {
              stopGuidedTour();
              navigate(step.to);
            }}
            onStartTour={async (stepId) => {
              await startTour(stepId);
            }}
          />

          <div className="flex flex-col gap-2">
            <p
              className="text-xs leading-4 text-[var(--oh-muted)]"
              data-testid="super-admin-setup-floating-next"
            >
              {nextLabel}
            </p>
            <BrandButton
              type="button"
              variant="primary"
              className="w-full"
              testId="super-admin-setup-floating-guide"
              isDisabled={tourStarting}
              onClick={async () => {
                await startTour();
              }}
            >
              {t(I18nKey.SUPER_ADMIN$SETUP_START_GUIDE)}
            </BrandButton>
          </div>
        </div>
      )}

      <button
        type="button"
        data-testid="super-admin-setup-floating-toggle"
        aria-label={t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-2 rounded-full border border-[var(--oh-border)]",
          "bg-base-secondary px-3.5 py-2 text-sm font-medium text-white",
          "hover:bg-surface-raised",
        )}
      >
        <ClipboardList className="size-4" strokeWidth={2} aria-hidden />
        {t(I18nKey.SUPER_ADMIN$SETUP_GUIDE)}
      </button>
    </div>
  );
}

function SetupStepRow({
  step,
  complete,
  tourStarting,
  onStartTour,
}: {
  step: SuperAdminSetupStep;
  complete: boolean;
  tourStarting: boolean;
  onStartTour: (stepId: SuperAdminSetupStepId) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div
      data-testid={`super-admin-setup-step-${step.id}`}
      className="group flex items-start gap-3 px-3 py-3"
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
      <button
        type="button"
        className="min-w-0 flex-1 space-y-1 rounded-md text-left hover:opacity-90"
        onClick={() => navigate(step.to)}
      >
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
      </button>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          data-testid={`super-admin-setup-tour-${step.id}`}
          aria-label={t(I18nKey.SUPER_ADMIN$SETUP_START_GUIDE)}
          title={t(I18nKey.SUPER_ADMIN$SETUP_START_GUIDE)}
          disabled={tourStarting}
          className={cn(
            "rounded-md border border-[var(--oh-border)] p-2",
            "text-[var(--oh-muted)] hover:bg-base hover:text-white",
            "opacity-0 group-hover:opacity-100 focus-visible:opacity-100",
            "disabled:opacity-50",
          )}
          onClick={() => {
            onStartTour(step.id);
          }}
        >
          <ChevronRight className="size-4" strokeWidth={2} aria-hidden />
        </button>
      </div>
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
  const [tourStarting, setTourStarting] = useState(false);
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
      <div className="flex flex-col items-start gap-2 sm:items-end">
        <p
          className="text-sm text-[var(--oh-muted)]"
          data-testid="super-admin-setup-next"
        >
          {nextStep
            ? t(I18nKey.SUPER_ADMIN$SETUP_NEXT, {
                step: t(nextStep.title as I18nKey),
              })
            : t(I18nKey.SUPER_ADMIN$SETUP_COMPLETE)}
        </p>
        <BrandButton
          type="button"
          variant="primary"
          testId="super-admin-setup-start-guide"
          className="shrink-0"
          isDisabled={tourStarting}
          onClick={async () => {
            setTourStarting(true);
            try {
              await runSuperAdminSetupTour(
                navigate,
                nextStep?.id ?? SUPER_ADMIN_SETUP_STEPS[0]?.id,
              );
            } finally {
              setTourStarting(false);
            }
          }}
        >
          {t(I18nKey.SUPER_ADMIN$SETUP_START_GUIDE)}
        </BrandButton>
      </div>
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
            tourStarting={tourStarting}
            onStartTour={async (stepId) => {
              setTourStarting(true);
              try {
                await runSuperAdminSetupTour(navigate, stepId);
              } finally {
                setTourStarting(false);
              }
            }}
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
