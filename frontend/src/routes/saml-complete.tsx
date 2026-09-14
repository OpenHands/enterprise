import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useCompleteSaml } from "#/hooks/mutation/use-native-auth";
import { useConfig } from "#/hooks/query/use-config";
import {
  AuthError,
  AuthFrame,
} from "#/components/features/native-auth/auth-form";
import { getSafeReturnTo } from "#/utils/safe-return-to";
import { navigateOrHardRedirect } from "#/utils/cross-app-redirect";
import { samlErrorMessage } from "#/components/features/native-auth/saml-error";

export const meta = (): { name: string; content: string }[] => [
  { name: "referrer", content: "no-referrer" },
];

export default function SamlComplete(): React.JSX.Element {
  const { t } = useTranslation();
  const { data: config, isLoading } = useConfig({ enabled: true });
  const complete = useCompleteSaml();
  const started = useRef(false);
  const [errorMessage, setErrorMessage] = useState<ReturnType<
    typeof samlErrorMessage
  > | null>(null);
  const client = useQueryClient();
  const navigate = useNavigate();

  useEffect(() => {
    if (isLoading || started.current) return;
    started.current = true;
    if (
      config?.auth_mode !== "native" ||
      !config.login_methods?.includes("saml")
    ) {
      setErrorMessage("NATIVE_AUTH$SSO_FAILED");
      return;
    }
    // Backend cookies bind completion to this browser. No assertion, relay
    // state, authorization code or invitation token is exposed to this page.
    complete
      .run()
      .then(async (result) => {
        await client.cancelQueries();
        client.removeQueries({
          predicate: (query) => query.queryKey[0] !== "web-client-config",
        });
        client.setQueryData(["user", "authenticated", "saas", "native"], {
          authenticated: true,
          acceptedTos: result.accepted_tos,
        });
        const destination = getSafeReturnTo(
          new URLSearchParams({ returnTo: result.redirect_to }),
        );
        navigateOrHardRedirect(navigate, destination, { replace: true });
      })
      .catch((error: unknown): void =>
        setErrorMessage(samlErrorMessage(error)),
      );
  }, [isLoading, config?.auth_mode, config?.login_methods]);

  return (
    <AuthFrame title={t("NATIVE_AUTH$SIGN_IN")}>
      {errorMessage ? (
        <>
          <AuthError message={t(errorMessage)} />
          <Link to="/login" className="text-primary underline">
            {t("NATIVE_AUTH$BACK_TO_LOGIN")}
          </Link>
        </>
      ) : (
        <p role="status">{t("NATIVE_AUTH$SSO_COMPLETING")}</p>
      )}
    </AuthFrame>
  );
}
