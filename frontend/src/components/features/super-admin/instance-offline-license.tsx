import { useTranslation } from "react-i18next";
import { formatLicenseDate } from "#/components/features/settings/license-banner";
import { useConfig } from "#/hooks/query/use-config";
import { useLicenseStatus } from "#/hooks/query/use-license-status";
import { I18nKey } from "#/i18n/declaration";

/** Says that the install uses an offline license file, and when it ends. */
export function InstanceOfflineLicense() {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { data: status } = useLicenseStatus({
    enabled: config?.app_mode === "saas",
  });

  if (!status?.offline) return null;

  return (
    <div
      className="flex flex-col gap-1 text-sm"
      data-testid="instance-offline-license"
    >
      <p>{t(I18nKey.SUPER_ADMIN$INSTANCE_OFFLINE_LICENSE)}</p>
      {status.license_ends_at ? (
        <p className="text-xs text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$INSTANCE_LICENSE_ENDS_AT, {
            date: formatLicenseDate(status.license_ends_at),
          })}
        </p>
      ) : null}
    </div>
  );
}
