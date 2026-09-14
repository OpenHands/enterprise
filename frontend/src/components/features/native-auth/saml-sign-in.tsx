import { FaUserShield } from "react-icons/fa";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LoginButton, loginButtonLabelClassName } from "../auth/login-button";
import { useStartSaml } from "#/hooks/mutation/use-native-auth";
import { NativeAuthError } from "#/api/native-auth-service/native-auth-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { AuthError } from "./auth-form";
import { samlErrorMessage } from "./saml-error";

export function SamlSignIn({
  loginPresentation = false,
  returnTo = "/",
  invitationToken,
  link = false,
  reauthenticate = false,
  onReauthenticationRequired,
}: {
  loginPresentation?: boolean;
  returnTo?: string;
  invitationToken?: string;
  link?: boolean;
  reauthenticate?: boolean;
  onReauthenticationRequired?: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();
  const start = useStartSaml();
  const [errorMessage, setErrorMessage] = useState<ReturnType<
    typeof samlErrorMessage
  > | null>(null);

  const signIn = async (): Promise<void> => {
    setErrorMessage(null);
    try {
      const { redirect_to: destination } = await start.run({
        return_path: returnTo,
        ...(invitationToken ? { invitation_token: invitationToken } : {}),
        ...(link ? { link: true } : {}),
        ...(reauthenticate ? { reauthenticate: true } : {}),
      });
      const url = new URL(destination);
      if (!["https:", "http:"].includes(url.protocol)) throw new Error();
      // The server selects the trusted IdP. The SAML request stays out of
      // component state, query caches, local storage and analytics events.
      window.location.assign(destination);
    } catch (error) {
      if (
        link &&
        onReauthenticationRequired &&
        error instanceof NativeAuthError &&
        [401, 403].includes(error.status) &&
        (!error.code || error.code === "recent_auth_required")
      ) {
        onReauthenticationRequired();
      } else setErrorMessage(samlErrorMessage(error));
    }
  };

  return (
    <div className="ph-no-capture ph-mask flex flex-col gap-3">
      <AuthError message={errorMessage ? t(errorMessage) : null} />
      {loginPresentation ? (
        <LoginButton
          className="bg-[#374151] text-white"
          disabled={start.isPending}
          onClick={signIn}
        >
          <FaUserShield size={14} className="shrink-0" />
          <span className={loginButtonLabelClassName}>
            {t("NATIVE_AUTH$SIGN_IN_SSO")}
          </span>
        </LoginButton>
      ) : (
        <BrandButton
          type="button"
          variant="secondary"
          className="w-full"
          isDisabled={start.isPending}
          onClick={signIn}
        >
          {t(link ? "NATIVE_AUTH$LINK_SSO" : "NATIVE_AUTH$SIGN_IN_SSO")}
        </BrandButton>
      )}
    </div>
  );
}
