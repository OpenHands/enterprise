/* eslint-disable i18next/no-literal-string */
/**
 * TEMPORARY floating QA panel for Setup / NUX / persona testing.
 * Only mounts when VITE_MOCK_API=true. Remove before production.
 */

import { useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router";
import { FlaskConical, X } from "lucide-react";
import { stopGuidedTour } from "#/components/features/setup/tours/tour-engine";
import {
  PRODUCT_TOUR_DISMISSED_KEY,
  type SetupPersona,
} from "#/utils/org/setup-readiness";
import {
  getSuperAdminNuxPath,
  resetSuperAdminNux,
  setSuperAdminNuxStep,
  type SuperAdminNuxStep,
} from "#/utils/org/super-admin-nux";
import {
  resetSuperAdminSetupState,
  setSuperAdminSetupVisible,
} from "#/components/features/super-admin/super-admin-setup";
import {
  SETUP_TEST_PERSONA_OPTIONS,
  isSetupTestHarnessEnabled,
  readSetupTestPersona,
  setSetupTestPersona,
  subscribeSetupTestPersona,
  type SetupTestPersona,
} from "#/utils/org/setup-test-harness";
import { cn } from "#/utils/utils";

const NUX_JUMPS: { step: SuperAdminNuxStep; label: string }[] = [
  { step: "welcome", label: "Welcome" },
  { step: "tos", label: "TOS" },
  { step: "account", label: "Account" },
  { step: "done", label: "Setup" },
];

function clearProductTourDismissed() {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.removeItem(PRODUCT_TOUR_DISMISSED_KEY);
}

function personaHomePath(persona: SetupTestPersona): string {
  if (persona === "super_admin") {
    return "/super-admin/setup";
  }
  return "/settings/getting-started";
}

function isMockApiBoot(): boolean {
  return import.meta.env.VITE_MOCK_API === "true";
}

export function SetupTestHarnessPanel() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(true);
  const persona = useSyncExternalStore(
    subscribeSetupTestPersona,
    readSetupTestPersona,
    () => "live" as SetupTestPersona,
  );

  // UI only in mock SaaS/dev — never in production builds.
  if (!isMockApiBoot() || !isSetupTestHarnessEnabled()) {
    return null;
  }

  const applyPersona = (next: SetupTestPersona) => {
    stopGuidedTour();
    setSetupTestPersona(next);
    if (next === "super_admin") {
      setSuperAdminNuxStep("done");
      setSuperAdminSetupVisible(true);
    }
    navigate(personaHomePath(next));
  };

  const jumpNux = (step: SuperAdminNuxStep) => {
    stopGuidedTour();
    setSetupTestPersona("super_admin");
    setSuperAdminNuxStep(step);
    setSuperAdminSetupVisible(true);
    navigate(getSuperAdminNuxPath(step));
  };

  const resetAll = () => {
    stopGuidedTour();
    resetSuperAdminNux();
    resetSuperAdminSetupState();
    clearProductTourDismissed();
    setSetupTestPersona("live");
    setSuperAdminSetupVisible(true);
    navigate("/install");
  };

  return (
    <div
      data-testid="setup-test-harness"
      className="pointer-events-auto fixed right-4 top-4 z-[10050] flex flex-col items-end gap-2"
    >
      {open && (
        <div
          data-testid="setup-test-harness-panel"
          className={cn(
            "w-[280px] rounded-lg border border-amber-500/50",
            "bg-base-secondary/95 p-3 shadow-xl backdrop-blur-sm",
          )}
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-1.5">
              <FlaskConical
                className="size-3.5 shrink-0 text-amber-400"
                strokeWidth={2}
                aria-hidden
              />
              <p className="truncate text-xs font-semibold uppercase tracking-wide text-amber-300">
                Setup test
              </p>
            </div>
            <button
              type="button"
              aria-label="Close setup test panel"
              className="rounded p-0.5 text-[var(--oh-muted)] hover:text-white"
              onClick={() => setOpen(false)}
            >
              <X className="size-3.5" strokeWidth={2} aria-hidden />
            </button>
          </div>

          <p className="mb-2 text-[10px] leading-4 text-[var(--oh-muted)]">
            Mock-only. Remove before production.
          </p>

          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-[var(--oh-muted)]">
            User type
          </p>
          <div
            className="mb-3 grid grid-cols-2 gap-1"
            role="group"
            aria-label="Setup test persona"
          >
            {SETUP_TEST_PERSONA_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                data-testid={`setup-test-persona-${option.id}`}
                onClick={() => applyPersona(option.id)}
                className={cn(
                  "rounded px-2 py-1.5 text-left text-[11px] font-medium",
                  "border transition-colors",
                  persona === option.id
                    ? "border-amber-400 bg-amber-400/15 text-amber-100"
                    : "border-[var(--oh-border)] text-[var(--oh-muted)] hover:border-amber-500/40 hover:text-white",
                )}
              >
                {option.label}
              </button>
            ))}
          </div>

          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-[var(--oh-muted)]">
            SA NUX page
          </p>
          <div className="mb-3 flex flex-wrap gap-1">
            {NUX_JUMPS.map((jump) => (
              <button
                key={jump.step}
                type="button"
                data-testid={`setup-test-nux-${jump.step}`}
                onClick={() => jumpNux(jump.step)}
                className={cn(
                  "rounded border border-[var(--oh-border)] px-2 py-1",
                  "text-[11px] font-medium text-[var(--oh-muted)]",
                  "hover:border-amber-500/40 hover:text-white",
                )}
              >
                {jump.label}
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-1">
            <button
              type="button"
              data-testid="setup-test-goto-getting-started"
              onClick={() => {
                stopGuidedTour();
                navigate("/settings/getting-started");
              }}
              className={cn(
                "rounded border border-[var(--oh-border)] px-2 py-1.5 text-left",
                "text-[11px] font-medium text-[var(--oh-muted)]",
                "hover:border-amber-500/40 hover:text-white",
              )}
            >
              Open Getting Started
            </button>
            <button
              type="button"
              data-testid="setup-test-reset-sa-setup"
              onClick={() => {
                stopGuidedTour();
                resetSuperAdminSetupState();
                setSuperAdminSetupVisible(true);
                clearProductTourDismissed();
              }}
              className={cn(
                "rounded border border-[var(--oh-border)] px-2 py-1.5 text-left",
                "text-[11px] font-medium text-[var(--oh-muted)]",
                "hover:border-amber-500/40 hover:text-white",
              )}
            >
              Reset SA setup + tour
            </button>
            <button
              type="button"
              data-testid="setup-test-reset-all"
              onClick={resetAll}
              className={cn(
                "rounded border border-amber-500/40 px-2 py-1.5 text-left",
                "text-[11px] font-medium text-amber-200",
                "hover:bg-amber-400/10",
              )}
            >
              Reset all → Welcome
            </button>
          </div>

          {persona !== "live" && (
            <p className="mt-2 text-[10px] text-amber-200/80">
              Override: {(persona as SetupPersona).replace("_", " ")}
            </p>
          )}
        </div>
      )}

      <button
        type="button"
        data-testid="setup-test-harness-toggle"
        aria-expanded={open}
        aria-label="Toggle setup test panel"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border border-amber-500/50",
          "bg-base-secondary px-3 py-1.5 text-xs font-semibold text-amber-200",
          "shadow-lg hover:bg-amber-400/10",
        )}
      >
        <FlaskConical className="size-3.5" strokeWidth={2} aria-hidden />
        Test
      </button>
    </div>
  );
}
