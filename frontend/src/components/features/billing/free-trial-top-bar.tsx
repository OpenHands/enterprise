import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";
import { Typography } from "#/ui/typography";

const PRICING_URL = "https://openhands.dev/pricing";
const CONTACT_SALES_PATH = "/information-request";

/** Polyform Free Trial default when no expiry is wired yet. */
export const DEFAULT_FREE_TRIAL_DAYS_LEFT = 30;

const barButtonClassName = cn(
  "inline-flex h-6 items-center justify-center rounded-md px-2.5",
  "text-xs font-semibold transition-opacity hover:opacity-80",
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
);

interface FreeTrialTopBarProps {
  daysLeft?: number;
}

export function FreeTrialTopBar({
  daysLeft = DEFAULT_FREE_TRIAL_DAYS_LEFT,
}: FreeTrialTopBarProps) {
  const { t } = useTranslation();

  return (
    <div
      data-testid="free-trial-top-bar"
      role="region"
      aria-label={t(I18nKey.FREE_TRIAL$BAR_LABEL)}
      className={cn(
        "flex w-full shrink-0 flex-wrap items-center justify-between gap-2",
        "bg-blue-600 px-3 py-1 text-white",
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <Typography.Text className="text-xs font-semibold text-white">
          {t(I18nKey.FREE_TRIAL$BAR_LABEL)}
        </Typography.Text>
        <Typography.Text
          testId="free-trial-days-left"
          className="text-xs font-medium text-white/90"
        >
          {t(I18nKey.FREE_TRIAL$DAYS_LEFT, { count: daysLeft })}
        </Typography.Text>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <a
          href={PRICING_URL}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="free-trial-choose-plan"
          className={cn(
            barButtonClassName,
            "bg-white text-blue-700",
            "focus-visible:outline-white",
          )}
        >
          {t(I18nKey.FREE_TRIAL$CHOOSE_PLAN)}
        </a>
        <Link
          to={CONTACT_SALES_PATH}
          data-testid="free-trial-contact-sales"
          className={cn(
            barButtonClassName,
            "border border-white/50 bg-transparent text-white",
            "focus-visible:outline-white",
          )}
        >
          {t(I18nKey.FREE_TRIAL$CONTACT_SALES)}
        </Link>
      </div>
    </div>
  );
}
