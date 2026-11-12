import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  licenseService,
  type LicenseUpdate,
} from "#/api/license-service/license-service.api";
import { BrandButton } from "#/components/features/settings/brand-button";
import {
  formatLicenseDate,
  getLicenseBannerMessage,
} from "#/components/features/settings/license-banner";
import { SettingsInput } from "#/components/features/settings/settings-input";
import { useSaveLicense } from "#/hooks/mutation/use-save-license";
import { useConfig } from "#/hooks/query/use-config";
import { useConfiguredLicense } from "#/hooks/query/use-configured-license";
import {
  LICENSE_QUERY_KEYS,
  useLicenseStatus,
} from "#/hooks/query/use-license-status";
import { I18nKey } from "#/i18n/declaration";
import { cn } from "#/utils/utils";

interface LicenseSettingsProps {
  /** Called after a license is saved, unless the server finds it invalid. */
  onSaved?: () => void;
}

/**
 * Shows the license this install uses, and lets a Super Admin save a license
 * key or an offline license file in its place. A license set in the install's
 * environment can only be changed there.
 */
export function LicenseSettings({ onSaved }: LicenseSettingsProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data: config } = useConfig();
  const { data: license } = useConfiguredLicense();
  const { data: status } = useLicenseStatus({
    enabled: config?.app_mode === "saas",
  });
  const { mutate: saveLicense, isPending } = useSaveLicense();
  const [licenseKey, setLicenseKey] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const save = (update: LicenseUpdate) =>
    saveLicense(update, {
      onSuccess: async () => {
        setLicenseKey("");
        // Saving an invalid license still succeeds; recheck before advancing.
        let checked;
        try {
          checked = await queryClient.fetchQuery({
            queryKey: LICENSE_QUERY_KEYS.status,
            queryFn: licenseService.getStatus,
            staleTime: 0,
          });
        } catch {
          // Without a status the license may be invalid, so don't advance.
          return;
        }
        if (checked.state !== "invalid") {
          onSaved?.();
        }
      },
    });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const key = licenseKey.trim();
    if (key) {
      save({ license_key: key });
    }
  };

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const input = event.target;
    const file = input.files?.[0];
    input.value = "";
    if (file) {
      save({ offline_license: await file.text() });
    }
  };

  // The form below is where a missing license gets fixed, so the banner's
  // "set the environment variable" message is left out here.
  const message =
    license?.kind && status?.state !== "missing_key"
      ? getLicenseBannerMessage(status)
      : null;

  return (
    <div className="flex flex-col gap-6" data-testid="license-settings">
      {license ? (
        <div
          className="flex flex-col gap-1 text-sm"
          data-testid="license-current"
        >
          {license.kind === null ? (
            <p className="text-[var(--oh-muted)]">
              {t(I18nKey.LICENSE$NONE_SET)}
            </p>
          ) : null}
          {license.kind === "key" ? (
            <p>
              <span className="text-[var(--oh-muted)]">
                {t(I18nKey.LICENSE$KEY_LABEL)}
              </span>{" "}
              <code data-testid="license-key-hint">{license.key_hint}…</code>
            </p>
          ) : null}
          {license.kind === "offline" ? (
            <p>{t(I18nKey.SUPER_ADMIN$INSTANCE_OFFLINE_LICENSE)}</p>
          ) : null}
          {license.kind && status?.state === "valid" ? (
            <p className="text-xs text-[var(--oh-muted)]">
              {t(I18nKey.LICENSE$VALID)}
            </p>
          ) : null}
          {message ? (
            <p
              data-testid="license-status"
              className={cn(
                "text-xs",
                message.tone === "info" && "text-[var(--oh-muted)]",
                message.tone === "warning" && "text-amber-200",
                message.tone === "error" && "text-[var(--oh-danger)]",
              )}
            >
              {t(message.key, {
                date: message.date ? formatLicenseDate(message.date) : "",
              })}
            </p>
          ) : null}
          {license.source === "environment" ? (
            <p
              className="text-xs text-[var(--oh-muted)]"
              data-testid="license-from-environment"
            >
              {t(I18nKey.LICENSE$FROM_ENVIRONMENT)}
            </p>
          ) : null}
        </div>
      ) : null}
      {license && license.source !== "environment" ? (
        <>
          <form className="flex items-end gap-3" onSubmit={onSubmit}>
            <SettingsInput
              testId="license-key-input"
              name="licenseKey"
              type="text"
              className="min-w-0 flex-1"
              label={t(I18nKey.LICENSE$KEY_LABEL)}
              placeholder="ohe_lic_..."
              autoComplete="off"
              value={licenseKey}
              onChange={setLicenseKey}
            />
            <BrandButton
              type="submit"
              variant="primary"
              testId="license-key-save"
              isDisabled={!licenseKey.trim() || isPending}
            >
              {t(I18nKey.BUTTON$SAVE)}
            </BrandButton>
          </form>
          <div className="flex flex-col items-start gap-2.5">
            <div className="flex flex-col gap-1">
              <p className="text-sm">{t(I18nKey.LICENSE$OFFLINE_LABEL)}</p>
              <p className="text-xs text-[var(--oh-muted)]">
                {t(I18nKey.LICENSE$OFFLINE_HINT)}
              </p>
            </div>
            <BrandButton
              type="button"
              variant="secondary"
              testId="license-offline-upload"
              isDisabled={isPending}
              onClick={() => fileInputRef.current?.click()}
            >
              {t(I18nKey.LICENSE$UPLOAD_OFFLINE)}
            </BrandButton>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="sr-only"
              tabIndex={-1}
              data-testid="license-offline-input"
              onChange={onFile}
            />
          </div>
        </>
      ) : null}
    </div>
  );
}
