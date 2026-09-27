export function nextBudgetResetDate(
  selectedDay: number,
  savedDay: number,
  savedEnd: string | undefined,
  now = new Date(),
): Date {
  if (selectedDay === savedDay && savedEnd) return new Date(savedEnd);
  const next = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), selectedDay),
  );
  if (next <= now) next.setUTCMonth(next.getUTCMonth() + 1);
  return next;
}
