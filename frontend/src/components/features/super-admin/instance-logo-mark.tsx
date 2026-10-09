import type { ReactNode } from "react";
import { cn } from "#/utils/utils";
import { useInstanceSettings } from "#/hooks/query/use-super-admin";

/**
 * The company image in place of the product mark, or `fallback` when none is
 * saved. Renders nothing while the instance settings load, so the product
 * mark does not flash before the company image.
 */
export function InstanceLogoMark({
  className,
  fallback = null,
}: {
  className?: string;
  fallback?: ReactNode;
}) {
  const { data: instanceSettings, isLoading } = useInstanceSettings();
  const logo = instanceSettings?.logo;
  if (isLoading) {
    return null;
  }
  if (!logo) {
    return fallback;
  }

  return (
    <img
      src={logo}
      alt=""
      data-testid="instance-logo-mark"
      className={cn(
        "size-7 max-w-none shrink-0 rounded-md object-cover",
        className,
      )}
    />
  );
}
