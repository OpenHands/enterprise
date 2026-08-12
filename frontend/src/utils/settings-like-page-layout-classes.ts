/**
 * Shared layout tokens for /settings/* pages so mobile gets horizontal inset
 * while desktop keeps the aside + `gap-10` + right gutter pattern.
 *
 * Matches agent-canvas settings layout spacing.
 *
 * `scrollbar-gutter: stable` keeps the gutter reserved when the page stops
 * overflowing. Without it, filtering a list short enough to fit drops the
 * scrollbar and the centered content jumps sideways into the freed space.
 */
export const settingsLikeMainScrollClassName =
  "flex min-h-0 min-w-0 flex-1 flex-col overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable] custom-scrollbar-always px-4 pt-8 pb-12 md:px-0 md:pr-[14px]";

/** Settings main column sits flush beside the bordered rail (drawer chrome). */
export const settingsLayoutMainScrollClassName =
  "flex min-h-0 min-w-0 flex-1 flex-col self-stretch overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable] custom-scrollbar-always px-4 pt-8 pb-12 md:px-0 md:pl-8 md:pr-[14px] md:pt-8";
