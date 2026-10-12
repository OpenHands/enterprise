import { cn } from "#/utils/utils";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
  settingsListRowClassName,
} from "#/utils/settings-list-classes";
import { InputSkeleton } from "../input-skeleton";
import { SubtextSkeleton } from "../subtext-skeleton";

interface GitSettingInputsSkeletonProps {
  /** SaaS shows provider cards; OSS shows token input groups. */
  variant?: "saas" | "oss";
}

function ProviderCardRowSkeleton() {
  return (
    <div className={cn(settingsListRowClassName, "justify-between gap-3")}>
      <div className="flex min-w-0 items-center gap-3">
        <div className="size-5 shrink-0 skeleton-round" />
        <div className="h-4 w-28 skeleton" />
      </div>
      <div className="h-7 w-24 shrink-0 skeleton" />
    </div>
  );
}

/** Mirrors {@link GitSettingsScreen} SaaS cards or OSS provider token groups. */
export function GitSettingInputsSkeleton({
  variant = "oss",
}: GitSettingInputsSkeletonProps) {
  if (variant === "saas") {
    return (
      <div
        data-testid="git-settings-skeleton"
        className="flex flex-col gap-3"
        aria-hidden
      >
        <div className="h-5 w-28 skeleton" />
        <div
          className={cn(
            settingsListContainerClassName,
            settingsListDividerClassName,
            "skeleton-stagger",
          )}
        >
          {Array.from({ length: 4 }, (_, index) => (
            <ProviderCardRowSkeleton key={`git-provider-skeleton-${index}`} />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div
      data-testid="git-settings-skeleton"
      className="skeleton-stagger flex flex-col gap-6"
      aria-hidden
    >
      {Array.from({ length: 6 }, (_, index) => (
        <div
          key={`git-token-skeleton-${index}`}
          className="flex flex-col gap-2.5"
        >
          <InputSkeleton />
          <SubtextSkeleton />
        </div>
      ))}
    </div>
  );
}
