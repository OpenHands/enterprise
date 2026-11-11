import { useConfig } from "#/hooks/query/use-config";
import { useLicenseStatus } from "#/hooks/query/use-license-status";

/** Whether the license lets the install add or turn on SSO login. */
export const useSsoLicensed = () => {
  const { data: config } = useConfig();
  const { data: status } = useLicenseStatus({
    enabled: config?.app_mode === "saas",
  });
  return {
    // True until the status loads, so nothing flickers off; the server checks
    // again on every change.
    ssoLicensed: status?.features.sso !== false,
    customerCenterUrl: status?.customer_center_url,
  };
};
