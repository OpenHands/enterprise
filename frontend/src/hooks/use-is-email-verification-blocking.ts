import { useConfig } from "./query/use-config";
import { useSettings } from "./query/use-settings";

/**
 * True when the user's access is restricted until they verify their email:
 * the server enforces verification (REQUIRE_VERIFIED_EMAILS) and the user's
 * email is explicitly unverified.
 */
export const useIsEmailVerificationBlocking = (): boolean => {
  const { data: config } = useConfig();
  const { data: settings } = useSettings();

  return (
    config?.require_verified_emails === true &&
    settings?.email_verified === false
  );
};
