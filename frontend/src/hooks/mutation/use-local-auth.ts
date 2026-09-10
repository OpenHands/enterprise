import { useMutation } from "@tanstack/react-query";
import AuthService from "#/api/auth-service/auth-service.api";

const meta = { disableToast: true };

export const usePasswordLogin = () =>
  useMutation({ mutationFn: AuthService.login, meta });
export const useChangePassword = () =>
  useMutation({ mutationFn: AuthService.changePassword, meta });
export const useForgotPassword = () =>
  useMutation({ mutationFn: AuthService.forgotPassword, meta });
export const useResetPassword = () =>
  useMutation({ mutationFn: AuthService.resetPassword, meta });
export const useEnrollAccount = () =>
  useMutation({ mutationFn: AuthService.enroll, meta });
export const useRequestEmailVerification = () =>
  useMutation({ mutationFn: AuthService.requestVerification, meta });
export const useVerifyEmail = () =>
  useMutation({ mutationFn: AuthService.verifyEmail, meta });
export const useChangeLoginEmail = () =>
  useMutation({ mutationFn: AuthService.changeEmail, meta });
export const useCreateAccount = () =>
  useMutation({ mutationFn: AuthService.createAccount, meta });
