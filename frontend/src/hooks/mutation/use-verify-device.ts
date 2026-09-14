import type { EphemeralMutation } from "#/hooks/mutation/use-ephemeral-mutation";
import AuthService from "#/api/auth-service/auth-service.api";
import { useEphemeralMutation } from "./use-ephemeral-mutation";

export const useVerifyDevice = (): EphemeralMutation<string, boolean> =>
  useEphemeralMutation(AuthService.verifyDevice);
