/** Matches left-aligned {@link SettingsSwitch} track size (40×22). */
export function SwitchSkeleton() {
  return (
    <div className="flex items-center gap-2">
      <div className="h-[22px] w-10 shrink-0 skeleton-round" />
      <div className="h-[20px] w-[100px] skeleton" />
    </div>
  );
}
