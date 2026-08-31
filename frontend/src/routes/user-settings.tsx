import React, { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { useSettings } from "#/hooks/query/use-settings";
import { SETTINGS_QUERY_KEYS } from "#/hooks/query/query-keys";
import { openHands } from "#/api/open-hands-axios";
import { displaySuccessToast } from "#/utils/custom-toast-handlers";
import { useEmailVerification } from "#/hooks/use-email-verification";
import { useSelectedOrganizationId } from "#/context/use-selected-organization";
import { useConfig } from "#/hooks/query/use-config";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { I18nKey } from "#/i18n/declaration";

// Email validation regex pattern
const EMAIL_REGEX = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

function EmailInputSection({
  email,
  onEmailChange,
  onSaveEmail,
  onResendVerification,
  isSaving,
  isResendingVerification,
  isEmailChanged,
  emailVerified,
  isEmailValid,
  emailChangeEnabled,
  children,
}: {
  email: string;
  onEmailChange: (value: string) => void;
  onSaveEmail: () => void;
  onResendVerification: () => void;
  isSaving: boolean;
  isResendingVerification: boolean;
  isEmailChanged: boolean;
  emailVerified?: boolean;
  isEmailValid: boolean;
  emailChangeEnabled: boolean;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-4">
      <div className="flex max-w-lg flex-col gap-3">
        <SettingsInput
          testId="email-input"
          type="email"
          label={t(I18nKey.SETTINGS$USER_EMAIL)}
          value={email}
          onChange={onEmailChange}
          isReadOnly={!emailChangeEnabled}
          placeholder={t(I18nKey.SETTINGS$USER_EMAIL_LOADING)}
          error={
            isEmailChanged && !isEmailValid
              ? t(I18nKey.SETTINGS$INVALID_EMAIL_FORMAT)
              : undefined
          }
        />

        <div className="flex flex-wrap items-center gap-3">
          {emailChangeEnabled && (
            <BrandButton
              type="button"
              variant="primary"
              testId="save-email-button"
              onClick={onSaveEmail}
              isDisabled={!isEmailChanged || isSaving || !isEmailValid}
              aria-busy={isSaving}
            >
              {isSaving ? t(I18nKey.SETTINGS$SAVING) : t(I18nKey.SETTINGS$SAVE)}
            </BrandButton>
          )}

          {emailVerified === false && (
            <BrandButton
              type="button"
              variant="primary"
              testId="resend-verification-button"
              onClick={onResendVerification}
              isDisabled={isResendingVerification}
              aria-busy={isResendingVerification}
            >
              {isResendingVerification
                ? t(I18nKey.SETTINGS$SENDING)
                : t(I18nKey.SETTINGS$RESEND_VERIFICATION)}
            </BrandButton>
          )}
        </div>

        {!emailChangeEnabled && (
          <p
            className="text-sm text-[var(--oh-muted)]"
            data-testid="email-change-disabled"
          >
            {t(I18nKey.SETTINGS$EMAIL_CHANGE_DISABLED)}
          </p>
        )}

        {children}
      </div>
    </div>
  );
}

function VerificationAlert() {
  const { t } = useTranslation();
  return (
    <div
      className="mt-1 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-red-300"
      role="alert"
    >
      <p className="font-medium">
        {t(I18nKey.SETTINGS$EMAIL_VERIFICATION_REQUIRED)}
      </p>
      <p className="text-sm text-red-300/90">
        {t(I18nKey.SETTINGS$EMAIL_VERIFICATION_RESTRICTION_MESSAGE)}
      </p>
    </div>
  );
}

function UserSettingsScreen() {
  const { t } = useTranslation();
  const { data: settings, isLoading, refetch } = useSettings();
  const { data: config } = useConfig();
  const { organizationId } = useSelectedOrganizationId();
  const [email, setEmail] = useState("");
  const [originalEmail, setOriginalEmail] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [isEmailValid, setIsEmailValid] = useState(true);
  const queryClient = useQueryClient();
  const pollingIntervalRef = useRef<number | null>(null);
  const prevVerificationStatusRef = useRef<boolean | undefined>(undefined);
  const { resendEmailVerification, isResendingVerification } =
    useEmailVerification();

  useEffect(() => {
    if (settings?.email) {
      setEmail(settings.email);
      setOriginalEmail(settings.email);
      setIsEmailValid(EMAIL_REGEX.test(settings.email));
    }
  }, [settings?.email]);

  useEffect(() => {
    if (pollingIntervalRef.current) {
      window.clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }

    if (
      prevVerificationStatusRef.current === false &&
      settings?.email_verified === true
    ) {
      displaySuccessToast(t(I18nKey.SETTINGS$EMAIL_VERIFIED_SUCCESSFULLY));
      setTimeout(() => {
        queryClient.invalidateQueries({
          queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
        });
      }, 2000);
    }

    prevVerificationStatusRef.current = settings?.email_verified;

    if (settings?.email_verified === false) {
      pollingIntervalRef.current = window.setInterval(() => {
        refetch();
      }, 5000);
    }

    return () => {
      if (pollingIntervalRef.current) {
        window.clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, [settings?.email_verified, refetch, queryClient, t, organizationId]);

  const handleEmailChange = (newEmail: string) => {
    setEmail(newEmail);
    setIsEmailValid(EMAIL_REGEX.test(newEmail));
  };

  const handleSaveEmail = async () => {
    if (email === originalEmail || !isEmailValid) return;
    try {
      setIsSaving(true);
      await openHands.post("/api/email", { email }, { withCredentials: true });
      setOriginalEmail(email);
      displaySuccessToast(t(I18nKey.SETTINGS$EMAIL_SAVED_SUCCESSFULLY));
      queryClient.invalidateQueries({
        queryKey: SETTINGS_QUERY_KEYS.personal(organizationId),
      });
    } catch (error) {
      // eslint-disable-next-line no-console
      console.error(t(I18nKey.SETTINGS$FAILED_TO_SAVE_EMAIL), error);
    } finally {
      setIsSaving(false);
    }
  };

  const handleResendVerification = () => {
    resendEmailVerification({});
  };

  const isEmailChanged = email !== originalEmail;
  const emailChangeEnabled = config?.email_change_enabled ?? true;

  return (
    <div data-testid="user-settings-screen" className="flex flex-col h-full">
      <div className="flex flex-col gap-6">
        {isLoading ? (
          <div className="h-9 w-64 max-w-full animate-pulse rounded-lg bg-tertiary" />
        ) : (
          <EmailInputSection
            email={email}
            onEmailChange={handleEmailChange}
            onSaveEmail={handleSaveEmail}
            onResendVerification={handleResendVerification}
            isSaving={isSaving}
            isResendingVerification={isResendingVerification}
            isEmailChanged={isEmailChanged}
            emailVerified={settings?.email_verified}
            isEmailValid={isEmailValid}
            emailChangeEnabled={emailChangeEnabled}
          >
            {settings?.email_verified === false && <VerificationAlert />}
          </EmailInputSection>
        )}
      </div>
    </div>
  );
}

export default UserSettingsScreen;
