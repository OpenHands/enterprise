import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  NavLink,
  useLocation,
  useNavigate,
  useSearchParams,
  type NavigateFunction,
} from "react-router";
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
import { useUpdateSetupState } from "#/hooks/mutation/use-super-admin-mutations";
import { canAccessSuperAdminDashboard } from "#/utils/org/super-admin-access";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";
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
  getNextSuperAdminSetupStep,
  useSuperAdminSetup,
  type SuperAdminSetupStep,
  type SuperAdminSetupStepId,
} from "./super-admin-setup";
import { SUPER_ADMIN_SETUP_TOUR } from "#/components/features/setup/tours/super-admin-setup-tour";
import { SUPER_ADMIN_SETUP_STEP_EVENT } from "#/components/features/setup/tours/types";
import {
  startGuidedTour,
  stopGuidedTour,
  isGuidedTourActive,
  subscribeGuidedTourActive,
} from "#/components/features/setup/tours/tour-engine";

/**
 * Names the setup step whose tour to start when a page loads. Agent Canvas
 * reads it on the links this guide opens, and this guide on the links Canvas
 * opens.
 */
const SETUP_TOUR_PARAM = "setup_tour";

function tourStopsFor(stepId: SuperAdminSetupStepId) {
  return SUPER_ADMIN_SETUP_TOUR.steps.filter(
    (stop) => stop.checklistId === stepId,
  );
}

async function runSuperAdminSetupTour(
  navigate: NavigateFunction,
  stepId: SuperAdminSetupStepId,
): Promise<void> {
  const stops = tourStopsFor(stepId);
  if (stops.length === 0) {
    // A step in Agent Canvas has no tour stop here: the page load into Canvas
    // would end the tour. Open the step and let Canvas start its own tour.
    const step = SUPER_ADMIN_SETUP_STEPS.find(({ id }) => id === stepId);
    if (step) {
      // /canvas/... steps need a page load: the in-app /canvas route shows an
      // error when the same URL is opened twice in a session.
      navigateOrHardRedirect(
        navigate,
        `${step.to}?${SETUP_TOUR_PARAM}=${step.id}`,
      );
    }
    return;
  }
  // One step at a time: once the step is done, the guide opens the next one.
  // Checklist completion is driven by real actions (e.g. LLM saved),
  // not by walking through spotlight tips.
  await startGuidedTour({ ...SUPER_ADMIN_SETUP_TOUR, steps: stops }, navigate);
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
  const { visible, nextStep, progress, completedCount, totalCount, refetch } =
    useSuperAdminSetup();
  const isSetupGuidePage = pathname === SUPER_ADMIN_PATHS.setup;
  // Closed on the setup guide itself, because that page already shows the
  // checklist. Elsewhere the panel starts open until the user dismisses it.
  const [open, setOpen] = useState(() => !isSetupGuidePage);
  const userClosedRef = useRef(false);
  const suppressOpenAfterTour = useRef(false);
  const [tourStarting, setTourStarting] = useState(false);
  const tourActive = useSyncExternalStore(
    subscribeGuidedTourActive,
    isGuidedTourActive,
    isGuidedTourActive,
  );

  const isInstallRoute = pathname.startsWith("/install");
  const canAccess = canAccessSuperAdminDashboard(
    config?.feature_flags,
    me?.permissions,
  );

  useEffect(() => {
    if (tourActive) {
      suppressOpenAfterTour.current = true;
      setOpen(false);
      return;
    }
    if (isSetupGuidePage) {
      suppressOpenAfterTour.current = false;
      setOpen(false);
      return;
    }
    if (suppressOpenAfterTour.current) {
      suppressOpenAfterTour.current = false;
      setOpen(false);
      return;
    }
    if (!userClosedRef.current) {
      setOpen(true);
    }
  }, [isSetupGuidePage, tourActive]);

  // Progress is read from the server, so re-read it as the admin moves around
  // and when an action the tour waits on succeeds.
  useEffect(() => {
    if (visible) {
      refetch();
    }
  }, [pathname, visible, refetch]);

  // When the guide's next step is done, open the step after it the way Start
  // does. A step finished out of order, or not confirmed by the server, only
  // refreshes the progress; nothing opens after the last required step.
  const nextStepId = nextStep?.id ?? null;
  useEffect(() => {
    if (!visible) {
      return undefined;
    }
    const onStep = async (event: Event) => {
      const completedId = (event as CustomEvent<{ id?: string }>).detail?.id;
      const { data } = await refetch();
      const guideSteps = data?.guide_steps;
      if (!guideSteps || !completedId || completedId !== nextStepId) {
        return;
      }
      const after = getNextSuperAdminSetupStep(guideSteps);
      if (after && after.id !== completedId) {
        await runSuperAdminSetupTour(navigate, after.id);
      }
    };
    window.addEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onStep);
    return () =>
      window.removeEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, onStep);
  }, [visible, refetch, nextStepId, navigate]);

  // A page opened from Agent Canvas's guide names the step whose tour to
  // start. Canvas steps have no tour here, so only enterprise steps start.
  const [searchParams, setSearchParams] = useSearchParams();
  const tourParam = searchParams.get(SETUP_TOUR_PARAM);
  const startedTourParam = useRef<string | null>(null);
  useEffect(() => {
    if (!visible || !tourParam || startedTourParam.current === tourParam) {
      return;
    }
    startedTourParam.current = tourParam;
    setSearchParams(
      (params) => {
        const rest = new URLSearchParams(params);
        rest.delete(SETUP_TOUR_PARAM);
        return rest;
      },
      { replace: true },
    );
    const step = SUPER_ADMIN_SETUP_STEPS.find(({ id }) => id === tourParam);
    if (step && tourStopsFor(step.id).length > 0) {
      runSuperAdminSetupTour(navigate, step.id);
    }
  }, [visible, tourParam, setSearchParams, navigate]);

  const setWidgetOpen = (next: boolean) => {
    userClosedRef.current = !next;
    setOpen(next);
  };

  const startTour = async (fromStepId?: SuperAdminSetupStepId) => {
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

  if (!canAccess || !visible || isInstallRoute || tourActive) {
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
                onClick={() => setWidgetOpen(false)}
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
              navigateOrHardRedirect(navigate, step.to);
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
        onClick={() => setWidgetOpen(!open)}
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
      <span
        data-testid={`super-admin-setup-check-${step.id}`}
        aria-hidden
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
          complete
            ? "border-foreground bg-foreground text-base"
            : "border-[var(--oh-border)] bg-transparent text-transparent",
        )}
      >
        <Check className="size-3" strokeWidth={3} aria-hidden />
      </span>
      <button
        type="button"
        className="min-w-0 flex-1 space-y-1 rounded-md text-left hover:opacity-90"
        onClick={() => navigateOrHardRedirect(navigate, step.to)}
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
  const {
    completed,
    nextStep,
    completedCount,
    totalCount,
    progress,
    active,
    isLoaded,
  } = useSuperAdminSetup();
  const { mutate: updateSetupState } = useUpdateSetupState();
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removeBecauseComplete, setRemoveBecauseComplete] = useState(false);
  const [tourStarting, setTourStarting] = useState(false);
  // Unknown until the server state loads, so opening a finished guide
  // does not count as finishing it.
  const wasComplete = useRef<boolean | null>(null);

  useEffect(() => {
    if (!isLoaded) {
      return;
    }
    if (progress === 1 && wasComplete.current === false && active) {
      setRemoveBecauseComplete(true);
      setRemoveOpen(true);
    }
    wasComplete.current = progress === 1;
  }, [progress, active, isLoaded]);

  const hideGuide = () => {
    setRemoveOpen(false);
    updateSetupState(
      { guide_dismissed: true },
      { onSuccess: () => navigate(SUPER_ADMIN_PATHS.root) },
    );
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
