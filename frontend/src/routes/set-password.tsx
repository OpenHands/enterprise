import React from "react";
import { AxiosError } from "axios";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import AuthService from "#/api/auth-service/auth-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { I18nKey } from "#/i18n/declaration";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

type Inspection = Awaited<ReturnType<typeof AuthService.inspectPasswordToken>>;

export default function SetPasswordPage() {
  const { t } = useTranslation();
  const [token, setToken] = React.useState("");
  const [inspection, setInspection] = React.useState<Inspection | null>(null);
  const [password, setPassword] = React.useState("");
  const [confirmation, setConfirmation] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  React.useEffect(() => {
    const linkToken = new URLSearchParams(window.location.hash.slice(1)).get(
      "token",
    );
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}${window.location.search}`,
    );
    if (!linkToken) {
      setInspection({
        status: "invalid",
        email: "",
        purpose: "setup",
        expires_at: null,
        minimum_password_length: 15,
      });
      return;
    }
    setToken(linkToken);
    AuthService.inspectPasswordToken(linkToken)
      .then(setInspection)
      .catch(() =>
        setInspection({
          status: "invalid",
          email: "",
          purpose: "setup",
          expires_at: null,
          minimum_password_length: 15,
        }),
      );
  }, []);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!inspection || password.length < inspection.minimum_password_length) {
      setError(
        t(I18nKey.AUTH$PASSWORD_MIN_LENGTH, {
          count: inspection?.minimum_password_length ?? 15,
        }),
      );
      return;
    }
    if (password !== confirmation) {
      setError(t(I18nKey.AUTH$PASSWORDS_DO_NOT_MATCH));
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      await AuthService.completePasswordToken({ token, password });
      window.location.assign("/");
    } catch (submitError) {
      setError(
        retrieveAxiosErrorMessage(submitError as AxiosError) ||
          t(I18nKey.AUTH$PASSWORD_LINK_INVALID),
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  let statusMessage = t(I18nKey.AUTH$PASSWORD_LINK_INVALID);
  if (inspection?.status === "used") {
    statusMessage = t(I18nKey.AUTH$PASSWORD_LINK_USED);
  } else if (inspection?.status === "expired") {
    statusMessage = t(I18nKey.AUTH$PASSWORD_LINK_EXPIRED);
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-base p-4">
      <section className="flex w-full max-w-md flex-col items-center gap-6 rounded-xl border border-tertiary bg-base-secondary p-8">
        <OpenHandsLogoWhite width={106} height={72} />
        {!inspection && <p className="text-muted">{t(I18nKey.HOME$LOADING)}</p>}
        {inspection && inspection.status !== "valid" && (
          <div className="flex flex-col items-center gap-4 text-center">
            <h1 className="text-2xl font-medium text-white">
              {t(I18nKey.AUTH$PASSWORD_LINK_UNAVAILABLE)}
            </h1>
            <p className="text-sm text-muted">{statusMessage}</p>
            <Link className="text-primary hover:underline" to="/login">
              {t(I18nKey.AUTH$RETURN_TO_LOGIN)}
            </Link>
          </div>
        )}
        {inspection?.status === "valid" && (
          <form className="flex w-full flex-col gap-4" onSubmit={handleSubmit}>
            <div className="text-center">
              <h1 className="text-2xl font-medium text-white">
                {inspection.purpose === "setup"
                  ? t(I18nKey.AUTH$SET_PASSWORD)
                  : t(I18nKey.AUTH$RESET_PASSWORD)}
              </h1>
              <p className="mt-2 text-sm text-muted">{inspection.email}</p>
            </div>
            <label className="flex flex-col gap-1 text-sm text-white">
              {t(I18nKey.AUTH$NEW_PASSWORD)}
              <input
                type="password"
                autoComplete="new-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-10 rounded border border-tertiary bg-base px-3"
              />
            </label>
            <p className="text-xs text-muted">
              {t(I18nKey.AUTH$PASSWORD_MIN_LENGTH, {
                count: inspection.minimum_password_length,
              })}
            </p>
            <label className="flex flex-col gap-1 text-sm text-white">
              {t(I18nKey.AUTH$CONFIRM_PASSWORD)}
              <input
                type="password"
                autoComplete="new-password"
                required
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                className="h-10 rounded border border-tertiary bg-base px-3"
              />
            </label>
            {error && (
              <p className="text-sm text-danger" role="alert">
                {error}
              </p>
            )}
            <BrandButton
              type="submit"
              variant="primary"
              isDisabled={isSubmitting}
            >
              {isSubmitting
                ? t(I18nKey.SETTINGS$SAVING)
                : t(I18nKey.AUTH$SAVE_PASSWORD)}
            </BrandButton>
          </form>
        )}
      </section>
    </main>
  );
}
