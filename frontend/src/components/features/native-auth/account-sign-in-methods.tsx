import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Typography } from "#/ui/typography";
import { useConfig } from "#/hooks/query/use-config";
import { useNativeProfile } from "#/hooks/query/use-native-profile";
import { NativeLoginForm } from "./auth-form";
import { SamlSignIn } from "./saml-sign-in";

export function AccountSignInMethods(): React.JSX.Element | null {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: profile, isError } = useNativeProfile();
  const [reauthenticate, setReauthenticate] = useState(false);
  if (isError) return <p role="alert">{t("NATIVE_AUTH$REQUEST_FAILED")}</p>;
  if (!profile) return null;
  const hasSaml = profile.authentication_methods?.includes("saml");
  const hasPassword =
    profile.has_password ?? !config?.login_methods?.includes("saml");

  return (
    <section className="max-w-[680px] flex flex-col gap-4">
      <Typography.H3 className="text-xl">
        {t("NATIVE_AUTH$SIGN_IN_METHODS")}
      </Typography.H3>
      {hasPassword && <p>{t("NATIVE_AUTH$EMAIL_PASSWORD_METHOD")}</p>}
      {hasSaml && <p>{t("NATIVE_AUTH$SSO_METHOD")}</p>}
      {hasSaml && !hasPassword && (
        <p className="text-sm text-tertiary-alt">
          {t("NATIVE_AUTH$SSO_PASSWORD_HELP")}
        </p>
      )}
      {config?.login_methods?.includes("saml") && !hasSaml && (
        <>
          <p className="text-sm text-tertiary-alt">
            {t("NATIVE_AUTH$LINK_SSO_HELP")}
          </p>
          {reauthenticate ? (
            <>
              <p>{t("NATIVE_AUTH$REAUTH_REQUIRED")}</p>
              <NativeLoginForm
                email={profile.email}
                reauthenticate
                onSuccess={() => setReauthenticate(false)}
              />
            </>
          ) : (
            <SamlSignIn
              link
              returnTo="/settings/user"
              onReauthenticationRequired={() => setReauthenticate(true)}
            />
          )}
        </>
      )}
    </section>
  );
}
