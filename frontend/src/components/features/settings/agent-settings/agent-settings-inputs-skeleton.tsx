import { cn } from "#/utils/utils";
import {
  formControlSwitchDescriptionClassName,
  formControlSwitchFieldClassName,
} from "#/utils/form-control-classes";
import { InputSkeleton } from "../input-skeleton";
import { SwitchSkeleton } from "../switch-skeleton";

/**
 * Mirrors the default OpenHands {@link AgentSettingsScreen} layout:
 * sub-agents toggle (+ helper), then parallel tool calls field.
 * Agent-type / ACP controls are feature-flagged and omitted from the
 * loading placeholder so the skeleton matches the common visible UI.
 */
export function AgentSettingsInputsSkeleton() {
  return (
    <div
      data-testid="agent-settings-skeleton"
      className="skeleton-stagger flex flex-col gap-6"
      aria-hidden
    >
      <section className="grid gap-4 xl:grid-cols-2">
        <div className={formControlSwitchFieldClassName}>
          <SwitchSkeleton />
          <div
            className={cn(
              formControlSwitchDescriptionClassName,
              "h-4 w-3/4 max-w-md skeleton",
            )}
          />
        </div>
      </section>
      <section className="grid gap-4 xl:grid-cols-2">
        <InputSkeleton />
      </section>
    </div>
  );
}
