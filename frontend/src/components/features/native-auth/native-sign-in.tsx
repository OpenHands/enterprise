import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FaEnvelope } from "react-icons/fa";
import { WebClientConfig } from "#/api/option-service/option.types";
import { LoginButton, loginButtonLabelClassName } from "../auth/login-button";
import { NativeLoginForm } from "./auth-form";
import { SamlSignIn } from "./saml-sign-in";

export function NativeSignIn({
  methods = ["password"],
  email,
  invitationToken,
  returnTo = "/",
  onSuccess,
}: {
  methods?: WebClientConfig["login_methods"];
  email?: string;
  invitationToken?: string;
  returnTo?: string;
  onSuccess?: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();
  const [emailSelected, setEmailSelected] = useState(false);
  const password = methods.includes("password");
  const saml = methods.includes("saml");
  return (
    <div className="flex flex-col gap-3">
      {password && (emailSelected || !saml) ? (
        <>
          <NativeLoginForm
            email={email}
            invitationToken={invitationToken}
            returnTo={returnTo}
            onSuccess={onSuccess}
          />
          {saml && (
            <button
              type="button"
              className="text-primary underline text-sm"
              onClick={() => setEmailSelected(false)}
            >
              {t("NATIVE_AUTH$OTHER_SIGN_IN_METHOD")}
            </button>
          )}
        </>
      ) : (
        password && (
          <LoginButton
            type="button"
            className="bg-[#9E28B0] text-white"
            onClick={() => setEmailSelected(true)}
          >
            <FaEnvelope size={14} className="shrink-0" />
            <span className={loginButtonLabelClassName}>
              {t("NATIVE_AUTH$SIGN_IN_EMAIL")}
            </span>
          </LoginButton>
        )
      )}
      {saml && (
        <SamlSignIn
          loginPresentation
          returnTo={returnTo}
          invitationToken={invitationToken}
        />
      )}
      {!password && !saml && <p>{t("NATIVE_AUTH$NO_LOGIN_METHODS")}</p>}
    </div>
  );
}
