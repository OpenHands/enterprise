import { cn } from "#/utils/utils";
import {
  formControlSwitchDescriptionClassName,
  formControlSwitchFieldClassName,
} from "#/utils/form-control-classes";
import { SwitchSkeleton } from "../switch-skeleton";

function SwitchFieldSkeleton() {
  return (
    <div className={formControlSwitchFieldClassName}>
      <SwitchSkeleton />
      <div
        className={cn(
          formControlSwitchDescriptionClassName,
          "h-4 w-3/4 max-w-md skeleton",
        )}
      />
    </div>
  );
}

/**
 * Mirrors Verification {@link SdkSectionPage}: Basic/Advanced/All tabs,
 * optional org-defaults banner, then the two critical toggles shown by
 * default (Confirmation Mode + Enable Critic).
 */
export function VerificationSettingsInputsSkeleton({
  showOrgBanner = false,
}: {
  showOrgBanner?: boolean;
}) {
  return (
    <div
      data-testid="verification-settings-skeleton"
      className="skeleton-stagger"
      aria-hidden
    >
      <div className="mb-6 flex items-center gap-2">
        <div className="h-9 w-14 skeleton" />
        <div className="h-9 w-20 skeleton" />
        <div className="h-9 w-10 skeleton" />
      </div>

      <div className="flex flex-col gap-6">
        {showOrgBanner ? (
          <div className="h-5 w-full max-w-2xl skeleton" />
        ) : null}
        <div className="grid gap-4 xl:grid-cols-2">
          <SwitchFieldSkeleton />
          <SwitchFieldSkeleton />
        </div>
      </div>
    </div>
  );
}
