import type { ReactNode } from "react";
import { useConfig } from "#/hooks/query/use-config";
import { useLicenseStatus } from "#/hooks/query/use-license-status";

/**
 * An inline link to Customer Center, for use in a `<Trans>` string. It shows
 * plain text until the license status gives the Customer Center URL.
 */
export function CustomerCenterLink({ children }: { children?: ReactNode }) {
  const { data: config } = useConfig();
  const { data: status } = useLicenseStatus({
    enabled: config?.app_mode === "saas",
  });

  if (!status?.customer_center_url) return children;

  return (
    <a
      href={status.customer_center_url}
      target="_blank"
      rel="noreferrer"
      className="underline underline-offset-2 hover:opacity-80"
      data-testid="customer-center-link"
    >
      {children}
    </a>
  );
}
