import { InputSkeleton } from "../input-skeleton";

/** Schema-style field grid used by LLM / condenser / verification SdkSection pages. */
export function LlmSettingsInputsSkeleton() {
  return (
    <div
      data-testid="llm-settings-skeleton"
      className="skeleton-stagger flex flex-col gap-6"
      aria-hidden
    >
      <div className="grid gap-4 xl:grid-cols-2">
        {Array.from({ length: 6 }, (_, index) => (
          <InputSkeleton key={`sdk-settings-skeleton-${index}`} />
        ))}
      </div>
    </div>
  );
}
