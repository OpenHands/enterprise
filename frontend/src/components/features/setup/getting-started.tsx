import { Check, ClipboardList } from "lucide-react";
import { NavLink, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import {
  SIDEBAR_ICON_SLOT_CLASS,
  SIDEBAR_ROW_INTERACTIVE_CLASS,
  navInteractiveTransitionClassName,
} from "#/components/features/sidebar/sidebar-layout";
import { useOrgSetup } from "#/hooks/query/use-org-setup";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { settingsListContainerClassName } from "#/utils/settings-list-classes";
import type { SetupStepDefinition } from "#/utils/org/setup-readiness";

const GETTING_STARTED_PATH = "/settings/getting-started";

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

export function SetupGuideWidget({ onNavigate }: { onNavigate?: () => void }) {
  const { t } = useTranslation();
  const { showSetup, checklist, progress } = useOrgSetup();

  if (!showSetup) {
    return null;
  }

  const nextLabel = checklist.nextStep
    ? t(I18nKey.SETUP$NEXT, {
        step: t(checklist.nextStep.titleKey),
      })
    : t(I18nKey.SETUP$COMPLETE);

  return (
    <NavLink
      to={GETTING_STARTED_PATH}
      onClick={onNavigate}
      data-testid="setup-guide-widget"
      aria-label={t(I18nKey.SETUP$GUIDE)}
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
            {t(I18nKey.SETUP$GUIDE)}
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

export function SetupGuideNav({
  onNavigate,
  className,
}: {
  onNavigate?: () => void;
  className?: string;
}) {
  const { showSetup } = useOrgSetup();
  if (!showSetup) {
    return null;
  }

  return (
    <div className={cn("mb-3", className)}>
      <div
        className={settingsListContainerClassName}
        data-testid="setup-guide-container"
      >
        <SetupGuideWidget onNavigate={onNavigate} />
      </div>
    </div>
  );
}

function SetupStepRow({
  step,
  complete,
}: {
  step: SetupStepDefinition;
  complete: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div
      data-testid={`setup-step-${step.id}`}
      className={cn(
        "flex items-start gap-3 px-3 py-3",
        complete && "opacity-70",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border",
          complete
            ? "border-foreground bg-foreground text-base"
            : "border-[var(--oh-border)] bg-transparent text-transparent",
        )}
        aria-hidden
      >
        <Check className="size-3" strokeWidth={3} />
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        <p
          className={cn(
            "text-sm font-medium leading-5",
            complete ? "text-[var(--oh-muted)] line-through" : "text-white",
          )}
        >
          {t(step.titleKey)}
        </p>
        {!complete && (
          <p className="text-sm leading-5 text-[var(--oh-muted)]">
            {t(step.descriptionKey)}
          </p>
        )}
      </div>
      {!complete && (
        <BrandButton
          type="button"
          variant="secondary"
          className="shrink-0"
          testId={`setup-step-action-${step.id}`}
          onClick={() => navigate(step.to)}
        >
          {t(step.actionLabelKey)}
        </BrandButton>
      )}
    </div>
  );
}

export function GettingStartedPage() {
  const { t } = useTranslation();
  const { checklist, progress, persona, markFinished, markAutomationDone } =
    useOrgSetup();

  const titleByPersona: Record<typeof persona, I18nKey> = {
    member: I18nKey.SETUP$TITLE_MEMBER,
    admin: I18nKey.SETUP$TITLE_ADMIN,
    owner: I18nKey.SETUP$TITLE_OWNER,
    super_admin: I18nKey.SETUP$TITLE_SUPER_ADMIN,
  };
  const titleKey = titleByPersona[persona];

  const canFinish =
    checklist.isCoreComplete || checklist.remaining.every((s) => !s.required);

  let progressLabel = t(I18nKey.SETUP$ALL_SET);
  if (!checklist.isCoreComplete && checklist.nextStep) {
    progressLabel = t(I18nKey.SETUP$PROGRESS, {
      current: Math.min(checklist.requiredDone + 1, checklist.requiredTotal),
      total: Math.max(checklist.requiredTotal, 1),
    });
  }

  return (
    <div className="flex flex-col gap-4" data-testid="getting-started-page">
      <div className="flex flex-col gap-1">
        <h2 className="text-lg font-semibold leading-6 text-white">
          {t(titleKey)}
        </h2>
        <p className="text-sm text-[var(--oh-muted)]">{progressLabel}</p>
      </div>

      <div className="flex items-center gap-3">
        <SetupProgressBar progress={progress} />
        <span className="shrink-0 text-xs tabular-nums text-[var(--oh-muted)]">
          {checklist.requiredDone}/{checklist.requiredTotal}
        </span>
      </div>

      <div
        className={cn(
          settingsListContainerClassName,
          "divide-y divide-[var(--oh-border)]",
        )}
      >
        {checklist.completed.map((step) => (
          <SetupStepRow key={step.id} step={step} complete />
        ))}
        {checklist.remaining.map((step) => (
          <SetupStepRow key={step.id} step={step} complete={false} />
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-4 border-t border-[var(--oh-border)] pt-6">
        <p className="min-w-0 text-sm leading-5 text-[var(--oh-muted)]">
          {canFinish
            ? t(I18nKey.SETUP$FINISH_HINT)
            : t(I18nKey.SETUP$CONTINUE_HINT)}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          {checklist.remaining.some((s) => s.id === "automation") && (
            <BrandButton
              type="button"
              variant="tertiary"
              testId="setup-skip-automation"
              onClick={markAutomationDone}
            >
              {t(I18nKey.SETUP$SKIP_AUTOMATION)}
            </BrandButton>
          )}
          <BrandButton
            type="button"
            variant="primary"
            testId="setup-finish"
            isDisabled={!canFinish}
            onClick={markFinished}
          >
            {t(I18nKey.SETUP$FINISH)}
          </BrandButton>
        </div>
      </div>
    </div>
  );
}
