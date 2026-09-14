import { NativeAuthService } from "#/api/native-auth-service/native-auth-service.api";
import {
  useEphemeralMutation,
  EphemeralMutationFor,
  EphemeralMutation,
} from "./use-ephemeral-mutation";

export const usePasswordLogin = (): EphemeralMutationFor<
  typeof NativeAuthService.login
> => useEphemeralMutation(NativeAuthService.login);
export const useStartSaml = (): EphemeralMutationFor<
  typeof NativeAuthService.startSaml
> => useEphemeralMutation(NativeAuthService.startSaml);
export const useCompleteSaml = (): EphemeralMutation<
  void,
  Awaited<ReturnType<typeof NativeAuthService.completeSaml>>
> =>
  useEphemeralMutation<
    void,
    Awaited<ReturnType<typeof NativeAuthService.completeSaml>>
  >(NativeAuthService.completeSaml);
export const useInspectEnrollment = (): EphemeralMutationFor<
  typeof NativeAuthService.inspect
> => useEphemeralMutation(NativeAuthService.inspect);
export const useCompleteEnrollment = (): EphemeralMutationFor<
  typeof NativeAuthService.enroll
> => useEphemeralMutation(NativeAuthService.enroll);
export const useCompletePasswordReset = (): EphemeralMutationFor<
  typeof NativeAuthService.resetPassword
> => useEphemeralMutation(NativeAuthService.resetPassword);
export const useChangePassword = (): EphemeralMutationFor<
  typeof NativeAuthService.changePassword
> => useEphemeralMutation(NativeAuthService.changePassword);
export const useIssueAccountInvitation = (): EphemeralMutationFor<
  typeof NativeAuthService.invite
> => useEphemeralMutation(NativeAuthService.invite);
export const useReissueAccountInvitation = (): EphemeralMutationFor<
  typeof NativeAuthService.reissue
> => useEphemeralMutation(NativeAuthService.reissue);
export const useRevokeAccountInvitation = (): EphemeralMutationFor<
  typeof NativeAuthService.revokeInvitation
> => useEphemeralMutation(NativeAuthService.revokeInvitation);
export const useIssuePasswordReset = (): EphemeralMutationFor<
  typeof NativeAuthService.issueReset
> => useEphemeralMutation(NativeAuthService.issueReset);
export const useChangeAccountState = (): EphemeralMutationFor<
  typeof NativeAuthService.lifecycle
> => useEphemeralMutation(NativeAuthService.lifecycle);
export const useSetSuperadmin = (): EphemeralMutationFor<
  typeof NativeAuthService.setSuperadmin
> => useEphemeralMutation(NativeAuthService.setSuperadmin);
