import { NativeAuthService } from "#/api/native-auth-service/native-auth-service.api";
import {
  useEphemeralMutation,
  EphemeralMutationFor,
} from "./use-ephemeral-mutation";

export const usePasswordLogin = (): EphemeralMutationFor<
  typeof NativeAuthService.login
> => useEphemeralMutation(NativeAuthService.login);
export const useInspectEnrollment = (): EphemeralMutationFor<
  typeof NativeAuthService.inspect
> => useEphemeralMutation(NativeAuthService.inspect);
export const useCompleteEnrollment = (): EphemeralMutationFor<
  typeof NativeAuthService.enroll
> => useEphemeralMutation(NativeAuthService.enroll);
export const useIssueAccountInvitation = (): EphemeralMutationFor<
  typeof NativeAuthService.invite
> => useEphemeralMutation(NativeAuthService.invite);
export const useReissueAccountInvitation = (): EphemeralMutationFor<
  typeof NativeAuthService.reissue
> => useEphemeralMutation(NativeAuthService.reissue);
export const useRevokeAccountInvitation = (): EphemeralMutationFor<
  typeof NativeAuthService.revokeInvitation
> => useEphemeralMutation(NativeAuthService.revokeInvitation);
