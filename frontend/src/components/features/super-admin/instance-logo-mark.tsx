import { cn } from "#/utils/utils";
import { useInstanceSettings } from "#/hooks/query/use-super-admin";

/** Company image beside the product mark. Renders nothing until one is saved. */
export function InstanceLogoMark({ className }: { className?: string }) {
  const { data: instanceSettings } = useInstanceSettings();
  const logo = instanceSettings?.logo;
  if (!logo) {
    return null;
  }

  return (
    <img
      src={logo}
      alt=""
      data-testid="instance-logo-mark"
      className={cn("size-7 shrink-0 rounded-md object-cover", className)}
    />
  );
}
