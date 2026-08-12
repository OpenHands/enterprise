import { ReactNode } from "react";
import { Text } from "#/ui/typography";
import { cn } from "#/utils/utils";
import { settingsListRowHoverClassName } from "#/utils/settings-list-classes";
import { formControlTransitionClassName } from "#/utils/form-control-classes";
import {
  IntegrationProviderIcon,
  type IntegrationProviderId,
} from "./integration-provider-icon";

interface IntegrationProviderCardProps {
  provider: IntegrationProviderId;
  title: string;
  /** Optional short supporting line under the title. */
  description?: string;
  /** Optional connected/disconnected status chip beside the title. */
  statusLabel?: string;
  isConnected?: boolean;
  statusTestId?: string;
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  "data-testid"?: string;
}

/**
 * Settings row for a single integration provider.
 * Meant to live inside a shared bordered list shell.
 */
export function IntegrationProviderCard({
  provider,
  title,
  description,
  statusLabel,
  isConnected,
  statusTestId,
  action,
  children,
  className,
  "data-testid": dataTestId,
}: IntegrationProviderCardProps) {
  return (
    <section data-testid={dataTestId} className={cn(className)}>
      <div
        className={cn(
          "flex items-center justify-between gap-4 px-3 py-3",
          formControlTransitionClassName,
          settingsListRowHoverClassName,
        )}
      >
        <div className="flex min-w-0 items-center gap-3">
          <IntegrationProviderIcon provider={provider} />
          <div className="flex min-w-0 flex-col gap-0.5">
            <div className="flex min-w-0 items-center gap-2">
              <Text className="truncate text-sm font-medium leading-5 text-content-2">
                {title}
              </Text>
              {statusLabel !== undefined ? (
                <span
                  data-testid={statusTestId}
                  className={cn(
                    "inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap",
                    isConnected
                      ? "bg-green-500/15 text-green-400"
                      : "bg-red-500/15 text-red-400",
                  )}
                >
                  {statusLabel}
                </span>
              ) : null}
            </div>
            {description ? (
              <Text className="text-xs leading-4 text-[var(--oh-muted)]">
                {description}
              </Text>
            ) : null}
          </div>
        </div>
        {action ? (
          <div className="flex shrink-0 justify-end">{action}</div>
        ) : null}
      </div>
      {children ? (
        <div className="border-t border-[var(--oh-border)] px-3 py-3">
          {children}
        </div>
      ) : null}
    </section>
  );
}
