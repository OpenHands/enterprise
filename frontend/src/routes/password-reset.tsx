import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useSecretFragment } from "#/hooks/use-secret-fragment";
import { useConfig } from "#/hooks/query/use-config";
import { useCompletePasswordReset } from "#/hooks/mutation/use-native-auth";
import {
  AuthFrame,
  AuthError,
  PasswordFields,
} from "#/components/features/native-auth/auth-form";
import {
  LoginButton,
  loginButtonLabelClassName,
} from "#/components/features/auth/login-button";

export const meta = (): { name: string; content: string }[] => [
  { name: "referrer", content: "no-referrer" },
];
export default function PasswordReset(): React.JSX.Element {
  const { t } = useTranslation();
  const [token, clearToken] = useSecretFragment();
  const { data: config } = useConfig({ enabled: true });
  const reset = useCompletePasswordReset();
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    if (fields.get("password") !== fields.get("confirmation")) {
      setError(t("NATIVE_AUTH$PASSWORD_MISMATCH"));
      return;
    }
    setError(null);
    try {
      await reset.run({ token, new_password: String(fields.get("password")) });
      form.reset();
      clearToken();
      setComplete(true);
    } catch (cause) {
      form.reset();
      setError(
        cause instanceof Error
          ? cause.message
          : t("NATIVE_AUTH$REQUEST_FAILED"),
      );
    }
  };
  return (
    <AuthFrame title={t("NATIVE_AUTH$RESET_PASSWORD")}>
      <AuthError message={error} />
      {complete ? (
        <p role="status">{t("NATIVE_AUTH$PASSWORD_RESET_COMPLETE")}</p>
      ) : (
        <>
          <p>{t("NATIVE_AUTH$LINK_UNAVAILABLE")}</p>
          {token && config?.auth_mode === "native" && (
            <form
              className="ph-no-capture ph-mask flex flex-col gap-4"
              onSubmit={submit}
            >
              <PasswordFields />
              <LoginButton
                type="submit"
                className="bg-[#9E28B0] text-white"
                disabled={reset.isPending}
              >
                <span className={loginButtonLabelClassName}>
                  {t("NATIVE_AUTH$RESET_PASSWORD")}
                </span>
              </LoginButton>
            </form>
          )}
        </>
      )}
      <Link to="/login" className="text-primary underline">
        {t("NATIVE_AUTH$BACK_TO_LOGIN")}
      </Link>
    </AuthFrame>
  );
}
