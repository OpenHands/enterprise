import { useState } from "react";
import { useTranslation } from "react-i18next";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { Typography } from "#/ui/typography";
import { useChangePassword } from "#/hooks/mutation/use-native-auth";
import { useNativeProfile } from "#/hooks/query/use-native-profile";
import { NativeAuthError } from "#/api/native-auth-service/native-auth-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { AuthError, PasswordFields, NativeLoginForm } from "./auth-form";

export function PasswordChange(): React.JSX.Element | null {
  const { t } = useTranslation();
  const change = useChangePassword();
  const { data: profile } = useNativeProfile();
  const [error, setError] = useState<string | null>(null);
  const [complete, setComplete] = useState(false);
  const [reauth, setReauth] = useState(false);
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
    setComplete(false);
    try {
      await change.run({
        current_password: String(fields.get("current_password")),
        new_password: String(fields.get("password")),
      });
      form.reset();
      setComplete(true);
    } catch (cause) {
      form.reset();
      if (cause instanceof NativeAuthError && [401, 403].includes(cause.status))
        setReauth(true);
      else
        setError(
          cause instanceof Error
            ? cause.message
            : t("NATIVE_AUTH$REQUEST_FAILED"),
        );
    }
  };
  // Missing capabilities on an older password-only backend retain its form.
  // Do not offer password operations until a federated profile confirms one.
  if (!profile || profile.has_password === false) return null;
  return (
    <section className="max-w-[680px] flex flex-col gap-4">
      <Typography.H3 className="text-xl">
        {t("NATIVE_AUTH$CHANGE_PASSWORD")}
      </Typography.H3>
      {complete && <p role="status">{t("NATIVE_AUTH$PASSWORD_CHANGED")}</p>}
      <AuthError message={error} />
      {reauth ? (
        <>
          <p>{t("NATIVE_AUTH$REAUTH_REQUIRED")}</p>
          <NativeLoginForm
            email={profile?.email}
            reauthenticate
            onSuccess={() => setReauth(false)}
          />
        </>
      ) : (
        <form
          className="ph-no-capture ph-mask flex flex-col gap-4"
          onSubmit={submit}
        >
          <SettingsInput
            className="w-full"
            label={t("NATIVE_AUTH$CURRENT_PASSWORD")}
            name="current_password"
            type="password"
            autoComplete="current-password"
            maxLength={1024}
            required
          />
          <PasswordFields />
          <BrandButton
            type="submit"
            variant="primary"
            isDisabled={change.isPending}
          >
            {t("NATIVE_AUTH$CHANGE_PASSWORD")}
          </BrandButton>
        </form>
      )}
    </section>
  );
}
