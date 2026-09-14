import { useTranslation } from "react-i18next";
import { Typography } from "#/ui/typography";
import { useNativeProfile } from "#/hooks/query/use-native-profile";

export function AccountSignInMethods(): React.JSX.Element | null {
  const { t } = useTranslation();
  const { data: profile, isError } = useNativeProfile();
  if (isError) return <p role="alert">{t("NATIVE_AUTH$REQUEST_FAILED")}</p>;
  if (!profile) return null;
  return (
    <section className="max-w-[680px] flex flex-col gap-4">
      <Typography.H3 className="text-xl">
        {t("NATIVE_AUTH$SIGN_IN_METHODS")}
      </Typography.H3>
      <p>{t("NATIVE_AUTH$EMAIL_PASSWORD_METHOD")}</p>
    </section>
  );
}
