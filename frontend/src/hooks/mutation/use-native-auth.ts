import { NativeAuthService } from "#/api/native-auth-service/native-auth-service.api";
import {
  useEphemeralMutation,
  EphemeralMutationFor,
} from "./use-ephemeral-mutation";

export const usePasswordLogin = (): EphemeralMutationFor<
  typeof NativeAuthService.login
> => useEphemeralMutation(NativeAuthService.login);
