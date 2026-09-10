import React from "react";
import axios from "axios";
import { useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { I18nKey } from "#/i18n/declaration";
import { BrandButton } from "#/components/features/settings/brand-button";

export function AccountCard({
  title,
  children,
}: React.PropsWithChildren<{ title: string }>) {
  return (
    <div className="w-full max-w-md flex flex-col items-center gap-6 rounded-lg border border-tertiary bg-base-secondary p-8">
      <OpenHandsLogoWhite width={80} height={54} />
      <h1 className="text-2xl font-semibold text-center">{title}</h1>
      {children}
    </div>
  );
}

export function AccountField({
  label,
  id,
  type,
  autoComplete,
  required,
}: React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  id: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        name={id}
        type={type}
        autoComplete={autoComplete}
        required={required}
        className="w-full rounded-sm border border-tertiary bg-base-tertiary p-2 text-white focus:outline focus:outline-primary"
      />
    </div>
  );
}

export function AccountSubmit({
  children,
  isPending,
}: React.PropsWithChildren<{ isPending: boolean }>) {
  const { t } = useTranslation();
  return (
    <BrandButton
      type="submit"
      variant="primary"
      isDisabled={isPending}
      className="w-full"
    >
      {isPending ? t(I18nKey.HOME$LOADING) : children}
    </BrandButton>
  );
}

export function AccountError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  let key = I18nKey.AUTH$ACTION_FAILED;
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    if (error.response?.status === 429) key = I18nKey.AUTH$TRY_LATER;
    else if (code === "invalid_credentials" || error.response?.status === 401)
      key = I18nKey.AUTH$INVALID_CREDENTIALS;
    else if (
      [
        "invalid_token",
        "expired_token",
        "invitation_invalid",
        "invitation_expired",
      ].includes(code)
    )
      key = I18nKey.AUTH$INVALID_ACTION_LINK;
    else if (
      ["invalid_password", "password_too_short", "password_too_weak"].includes(
        code,
      )
    )
      key = I18nKey.AUTH$PASSWORD_REQUIREMENTS;
  }
  return (
    <p role="alert" className="text-sm text-danger">
      {t(key)}
    </p>
  );
}
