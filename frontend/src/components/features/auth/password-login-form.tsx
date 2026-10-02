import React from "react";
import type { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import AuthService from "#/api/auth-service/auth-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import { I18nKey } from "#/i18n/declaration";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";

interface PasswordLoginFormProps {
  returnTo: string;
}

export function PasswordLoginForm({ returnTo }: PasswordLoginFormProps) {
  const { t } = useTranslation();
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await AuthService.loginWithPassword({ email, password });
      window.location.assign(returnTo);
    } catch (loginError) {
      setError(
        retrieveAxiosErrorMessage(loginError as AxiosError) ||
          t(I18nKey.AUTH$PASSWORD_LOGIN_ERROR),
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form
      className="flex w-[301.5px] flex-col gap-3"
      onSubmit={handleSubmit}
      data-testid="password-login-form"
    >
      <label className="flex flex-col gap-1 text-sm text-white">
        {t(I18nKey.AUTH$EMAIL)}
        <input
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className="h-10 rounded border border-tertiary bg-base-secondary px-3 text-white"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm text-white">
        {t(I18nKey.AUTH$PASSWORD)}
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className="h-10 rounded border border-tertiary bg-base-secondary px-3 text-white"
        />
      </label>
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <BrandButton type="submit" variant="primary" isDisabled={isSubmitting}>
        {isSubmitting ? t(I18nKey.AUTH$SIGNING_IN) : t(I18nKey.AUTH$SIGN_IN)}
      </BrandButton>
    </form>
  );
}
