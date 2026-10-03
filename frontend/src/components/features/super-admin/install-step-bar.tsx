import {
  Bot,
  Building2,
  FileText,
  Sparkles,
  UserRound,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

const INSTALL_STEPS: {
  id: string;
  label: I18nKey;
  detail: I18nKey;
  path: string;
  icon: LucideIcon;
}[] = [
  {
    id: "welcome",
    label: I18nKey.SA_NUX$STEP_WELCOME,
    detail: I18nKey.SA_NUX$STEP_WELCOME_DETAIL,
    path: "/install",
    icon: Sparkles,
  },
  {
    id: "tos",
    label: I18nKey.SA_NUX$STEP_TERMS,
    detail: I18nKey.SA_NUX$STEP_TERMS_DETAIL,
    path: "/install/tos",
    icon: FileText,
  },
  {
    id: "account",
    label: I18nKey.SA_NUX$STEP_ACCOUNT,
    detail: I18nKey.SA_NUX$STEP_ACCOUNT_DETAIL,
    path: "/install/account",
    icon: UserRound,
  },
  {
    id: "company",
    label: I18nKey.SA_NUX$STEP_COMPANY,
    detail: I18nKey.SA_NUX$STEP_COMPANY_DETAIL,
    path: "/install/company",
    icon: Building2,
  },
  {
    id: "org",
    label: I18nKey.SA_NUX$STEP_ORG,
    detail: I18nKey.SA_NUX$STEP_ORG_DETAIL,
    path: "/install/org",
    icon: Users,
  },
  {
    id: "llm",
    label: I18nKey.SA_NUX$STEP_LLM,
    detail: I18nKey.SA_NUX$STEP_LLM_DETAIL,
    path: "",
    icon: Bot,
  },
  {
    id: "automation",
    label: I18nKey.SA_NUX$STEP_AUTOMATION,
    detail: I18nKey.SA_NUX$STEP_AUTOMATION_DETAIL,
    path: "",
    icon: Workflow,
  },
];

function currentInstallStep(pathname: string) {
  const index = INSTALL_STEPS.findIndex((step) => {
    if (!step.path) {
      return false;
    }
    if (step.path === "/install") {
      return pathname === "/install";
    }
    return pathname === step.path || pathname.startsWith(`${step.path}/`);
  });
  return index === -1 ? 0 : index;
}

/**
 * Progress across the first-run screens. LLM setup and the first automation
 * stay ahead until those screens, so they read as the end of the path.
 */
function getStepStatus(index: number, current: number) {
  if (index < current) {
    return "done";
  }
  if (index === current) {
    return "current";
  }
  return "upcoming";
}

export function InstallStepBar({ pathname }: { pathname: string }) {
  const { t } = useTranslation();
  const current = currentInstallStep(pathname);
  const last = INSTALL_STEPS.length - 1;

  return (
    <nav
      aria-label={t(I18nKey.SA_NUX$STEP_PROGRESS)}
      data-testid="install-step-bar"
      className="pointer-events-none absolute inset-x-0 top-0 z-[1000] bg-base px-4 pb-3 pt-5"
    >
      <ol className="mx-auto flex w-full max-w-5xl items-start">
        {INSTALL_STEPS.map((step, index) => {
          const status = getStepStatus(index, current);
          const leftFilled = index > 0 && index <= current;
          const rightFilled = index < current;
          return (
            <li
              key={step.id}
              // Focusing a step reveals its label for keyboard users.
              // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
              tabIndex={0}
              data-testid={`install-step-${step.id}`}
              className="group pointer-events-auto relative flex min-w-0 flex-1 flex-col items-center outline-none"
              aria-current={status === "current" ? "step" : undefined}
              aria-describedby={`install-step-detail-${step.id}`}
            >
              <span
                className={cn(
                  "mb-2 text-sm tabular-nums leading-none transition-transform duration-200 group-hover:-translate-y-1 group-focus-visible:-translate-y-1",
                  status === "current" &&
                    "-translate-y-0.5 font-medium text-white [text-shadow:0_0_14px_rgba(255,255,255,0.7)] group-hover:[text-shadow:0_0_18px_rgba(255,255,255,0.9)]",
                  status === "done" &&
                    "font-medium text-white group-hover:[text-shadow:0_0_14px_rgba(255,255,255,0.65)]",
                  status === "upcoming" &&
                    "!text-[#979797] group-hover:!text-white group-hover:[text-shadow:0_0_14px_rgba(255,255,255,0.55)] group-focus-visible:!text-white",
                )}
              >
                {index + 1}
              </span>
              <div className="relative flex w-full items-center">
                <span
                  aria-hidden
                  className={cn(
                    "h-px flex-1 transition-colors duration-200",
                    index === 0 && "opacity-0",
                    leftFilled
                      ? "bg-white"
                      : "bg-[var(--oh-border)] group-hover:bg-white/70",
                  )}
                />
                <span
                  aria-hidden
                  className={cn(
                    "absolute left-1/2 size-1 -translate-x-1/2 rounded-full bg-white/50 transition duration-200 group-hover:scale-150 group-hover:bg-white group-hover:shadow-[0_0_12px_rgba(255,255,255,0.9)] group-focus-visible:scale-150 group-focus-visible:bg-white",
                    status === "current" &&
                      "scale-125 bg-white shadow-[0_0_12px_rgba(255,255,255,0.85)]",
                  )}
                />
                <span
                  aria-hidden
                  className={cn(
                    "h-px flex-1 transition-colors duration-200",
                    index === last && "opacity-0",
                    rightFilled
                      ? "bg-white"
                      : "bg-[var(--oh-border)] group-hover:bg-white/70",
                  )}
                />
              </div>
              <span
                className={cn(
                  "mt-2 max-w-[7.5rem] px-1 text-center text-[11px] leading-4 transition-colors duration-200",
                  status === "current"
                    ? "font-medium text-white"
                    : "!text-[#979797] group-hover:!text-white group-focus-visible:!text-white",
                )}
              >
                {t(step.label)}
              </span>
              <div
                id={`install-step-detail-${step.id}`}
                role="tooltip"
                className={cn(
                  "pointer-events-none absolute top-full z-10 w-56 pt-3 opacity-0 transition duration-200 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-visible:pointer-events-auto group-focus-visible:opacity-100",
                  index === 0 && "left-0",
                  index === last && "right-0",
                  index > 0 && index < last && "left-1/2 -translate-x-1/2",
                )}
              >
                <div className="rounded-xl border border-[var(--oh-border)] bg-base-secondary px-3 py-2.5 text-left shadow-[0_16px_40px_rgba(0,0,0,0.45)]">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden
                      className="flex size-6 shrink-0 items-center justify-center rounded-md border border-[var(--oh-border)] text-white"
                    >
                      <step.icon className="size-3.5" strokeWidth={1.75} />
                    </span>
                  </div>
                  <div className="mt-1 text-sm font-medium text-white">
                    {t(step.label)}
                  </div>
                  <div
                    className="mt-1 text-xs leading-5"
                    style={{ color: "var(--cool-grey-400, #979797)" }}
                  >
                    {t(step.detail)}
                  </div>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
