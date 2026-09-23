import { FreeTrialExpiredModal } from "#/components/features/billing/free-trial-expired-modal";
import {
  DEFAULT_FREE_TRIAL_DAYS_LEFT,
  FreeTrialTopBar,
} from "#/components/features/billing/free-trial-top-bar";

interface FreeTrialChromeProps {
  daysLeft?: number;
}

/**
 * Always shows the free-trial top bar. When the trial has ended
 * (daysLeft <= 0), also shows a blocking expired modal.
 */
export function FreeTrialChrome({
  daysLeft = DEFAULT_FREE_TRIAL_DAYS_LEFT,
}: FreeTrialChromeProps) {
  const isExpired = daysLeft <= 0;

  return (
    <>
      <FreeTrialTopBar daysLeft={Math.max(daysLeft, 0)} />
      {isExpired && <FreeTrialExpiredModal />}
    </>
  );
}
