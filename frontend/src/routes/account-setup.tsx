import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useAuthentication } from "#/hooks/use-authentication";
import { useSecretFragment } from "#/hooks/use-secret-fragment";
import { useConfig } from "#/hooks/query/use-config";
import {
  useCompleteEnrollment,
  useInspectEnrollment,
} from "#/hooks/mutation/use-native-auth";
import { Enrollment } from "#/api/native-auth-service/native-auth-service.api";
import {
  AuthFrame,
  AuthError,
  PasswordFields,
} from "#/components/features/native-auth/auth-form";
import { NativeSignIn } from "#/components/features/native-auth/native-sign-in";
import {
  LoginButton,
  loginButtonLabelClassName,
} from "#/components/features/auth/login-button";

export const meta = (): { name: string; content: string }[] => [
  { name: "referrer", content: "no-referrer" },
];

export default function AccountSetup(): React.JSX.Element {
  const { t } = useTranslation();
  const [token, clearToken] = useSecretFragment();
  const { data: config, isLoading } = useConfig({ enabled: true });
  const authentication = useAuthentication();
  const inspect = useInspectEnrollment();
  const enroll = useCompleteEnrollment();
  const started = useRef(false);
  const [invitation, setInvitation] = useState<Enrollment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const client = useQueryClient();

  useEffect(() => {
    if (
      started.current ||
      !token ||
      !authentication.accountActions.includes("enroll")
    )
      return;
    started.current = true;
    inspect
      .run(token)
      .then(setInvitation)
      .catch((cause: Error) => setError(cause.message));
  }, [token, authentication.accountActions]);

  const submit = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    if (fields.get("password") !== fields.get("confirmation")) {
      setError(t("AUTH$PASSWORD_MISMATCH"));
      return;
    }
    setError(null);
    try {
      const result = await enroll.run({
        token,
        password: String(fields.get("password")),
      });
      form.reset();
      if (result.action === "login") {
        // Another setup flow may have created credentials since inspection.
        // Refresh the available methods without retaining the invitation in a cache.
        setInvitation(await inspect.run(token));
        return;
      }
      clearToken();
      await client.cancelQueries();
      client.removeQueries({
        predicate: (query) => query.queryKey[0] !== "web-client-config",
      });
      client.setQueryData(["user", "authenticated", "saas", "native"], {
        authenticated: true,
        acceptedTos: "accepted_tos" in result ? result.accepted_tos : false,
      });
      navigate(result.redirect_to || "/", { replace: true });
    } catch (cause) {
      form.reset();
      setError(
        cause instanceof Error ? cause.message : t("AUTH$REQUEST_FAILED"),
      );
    }
  };

  return (
    <AuthFrame title={t("AUTH$SET_UP_ACCOUNT")}>
      <AuthError message={error} />
      {!isLoading &&
        (!token ||
          !authentication.accountActions.includes("enroll") ||
          (!!error && !invitation)) && <p>{t("AUTH$LINK_UNAVAILABLE")}</p>}
      {invitation && (
        <>
          <p>{invitation.email}</p>
          {invitation.org_name && (
            <p>{t("AUTH$INVITED_ORG", { name: invitation.org_name })}</p>
          )}
          {invitation.action === "login" ? (
            <>
              <p>{t("AUTH$EXISTING_ACCOUNT")}</p>
              <NativeSignIn
                methods={
                  invitation.authentication_methods?.filter((method) =>
                    (config?.login_methods || ["password"]).includes(method),
                  ) || config?.login_methods
                }
                email={invitation.email}
                invitationToken={token}
                onSuccess={clearToken}
              />
            </>
          ) : (
            <form
              onSubmit={submit}
              className="ph-no-capture ph-mask flex flex-col gap-4"
            >
              <PasswordFields />
              <LoginButton
                type="submit"
                className="bg-[#9E28B0] text-white"
                disabled={enroll.isPending}
              >
                <span className={loginButtonLabelClassName}>
                  {t("AUTH$SET_UP_ACCOUNT")}
                </span>
              </LoginButton>
            </form>
          )}
        </>
      )}
      {(isLoading || inspect.isPending) && (
        <p role="status">{t("HOME$LOADING")}</p>
      )}
      <Link to="/login" className="text-primary underline">
        {t("AUTH$BACK_TO_LOGIN")}
      </Link>
    </AuthFrame>
  );
}
