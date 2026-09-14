import {
  AuthenticationAdapter,
  selectAuthentication,
} from "#/api/auth-adapter";
import { useConfig } from "./query/use-config";

/** One selected session policy shared by query hooks and browser navigation. */
export function useAuthentication(): AuthenticationAdapter {
  const { data: config } = useConfig();
  return selectAuthentication(config?.auth_mode);
}
