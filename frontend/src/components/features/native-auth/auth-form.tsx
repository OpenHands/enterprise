import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { LoginButton, loginButtonLabelClassName } from "../auth/login-button";
import { usePasswordLogin } from "#/hooks/mutation/use-native-auth";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoginContent } from "#/components/features/auth/login-content";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";

export function AuthFrame({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <main className="min-h-screen flex items-center justify-center bg-base p-4">
      <LoginContent title={title}>{children}</LoginContent>
    </main>
  );
}

export function AuthError({
  message,
}: {
  message: string | null;
}): React.JSX.Element | null {
  return message ? (
    <p role="alert" className="text-sm text-red-400">
      {message}
    </p>
  ) : null;
}

export function NativeLoginForm({
  email,
  returnTo = "/",
  onSuccess,
  reauthenticate = false,
}: {
  email?: string;
  returnTo?: string;
  onSuccess?: () => void | Promise<void>;
  reauthenticate?: boolean;
}): React.JSX.Element {
  const { t } = useTranslation();
  const login = usePasswordLogin();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const passwordInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setError(null);
    try {
      const result = await login.run({
        email: String(fields.get("email")),
        password: String(fields.get("password")),
        return_path: returnTo,
      });
      form.reset();
      if (reauthenticate) {
        await onSuccess?.();
      } else {
        // A new account/session must not inherit the previous user's cached data.
        await queryClient.cancelQueries();
        queryClient.removeQueries({
          predicate: (query) => query.queryKey[0] !== "web-client-config",
        });
        queryClient.setQueryData(["user", "authenticated", "saas", "native"], {
          authenticated: true,
          acceptedTos: result.accepted_tos,
        });
        await onSuccess?.();
        navigateOrHardRedirect(navigate, result.redirect_to, { replace: true });
      }
    } catch (cause) {
      if (passwordInput.current) passwordInput.current.value = "";
      setError(
        cause instanceof Error ? cause.message : t("AUTH$REQUEST_FAILED"),
      );
    }
  };

  return (
    <form
      onSubmit={submit}
      className="ph-no-capture ph-mask flex flex-col gap-4"
    >
      <SettingsInput
        className="w-full"
        label={t("AUTH$EMAIL")}
        name="email"
        type="email"
        defaultValue={email}
        readOnly={!!email}
        autoComplete="username"
        required
      />
      <SettingsInput
        className="w-full"
        label={t("AUTH$PASSWORD")}
        name="password"
        type="password"
        autoComplete="current-password"
        inputRef={passwordInput}
        maxLength={1024}
        required
      />
      <AuthError message={error} />
      {reauthenticate ? (
        <BrandButton
          type="submit"
          variant="primary"
          isDisabled={login.isPending}
        >
          {t(reauthenticate ? "AUTH$CONFIRM_IDENTITY" : "AUTH$SIGN_IN")}
        </BrandButton>
      ) : (
        <LoginButton
          type="submit"
          className="bg-[#9E28B0] text-white"
          disabled={login.isPending}
        >
          <span className={loginButtonLabelClassName}>{t("AUTH$SIGN_IN")}</span>
        </LoginButton>
      )}

      {!reauthenticate && (
        <p className="text-sm text-tertiary-alt">{t("AUTH$CONTACT_ADMIN")}</p>
      )}
    </form>
  );
}
