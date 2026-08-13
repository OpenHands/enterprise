import { cn } from "#/utils/utils";
import {
  formControlSwitchDescriptionClassName,
  formControlSwitchFieldClassName,
} from "#/utils/form-control-classes";
import { InputSkeleton } from "../input-skeleton";
import { SwitchSkeleton } from "../switch-skeleton";

/** Mirrors {@link AppSettingsScreen}: language, toggles, sandbox fields, git block. */
export function AppSettingsInputsSkeleton() {
  return (
    <div
      data-testid="app-settings-skeleton"
      className="skeleton-stagger flex flex-col gap-6"
      aria-hidden
    >
      <InputSkeleton />
      <SwitchSkeleton />
      <SwitchSkeleton />
      <SwitchSkeleton />
      <SwitchSkeleton />
      <InputSkeleton />
      <InputSkeleton />

      <div className="mt-2 flex flex-col gap-6 border-t border-[var(--oh-border)] pt-6">
        <div className="flex flex-col gap-2">
          <div className="h-7 w-40 skeleton" />
          <div className="h-4 w-full max-w-xl skeleton" />
        </div>
        <div className="flex flex-col gap-2">
          <div className="h-5 w-36 skeleton" />
          <div className="h-4 w-full max-w-lg skeleton" />
          <div className={cn("mt-2", formControlSwitchFieldClassName)}>
            <SwitchSkeleton />
            <div
              className={cn(
                formControlSwitchDescriptionClassName,
                "h-4 w-2/3 max-w-md skeleton",
              )}
            />
          </div>
        </div>
        <InputSkeleton />
        <InputSkeleton />
      </div>
    </div>
  );
}
