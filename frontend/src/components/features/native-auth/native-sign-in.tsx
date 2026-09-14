import { useTranslation } from "react-i18next";
import { WebClientConfig } from "#/api/option-service/option.types";
import { NativeLoginForm } from "./auth-form";

export function NativeSignIn({
  methods = ["password"],
  email,
  returnTo = "/",
  onSuccess,
}: {
  methods?: WebClientConfig["login_methods"];
  email?: string;
  returnTo?: string;
  onSuccess?: () => void;
}): React.JSX.Element {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3">
      {methods.includes("password") ? (
        <NativeLoginForm
          email={email}
          returnTo={returnTo}
          onSuccess={onSuccess}
        />
      ) : (
        <p>{t("AUTH$NO_LOGIN_METHODS")}</p>
      )}
    </div>
  );
}
