import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useSettings } from "#/hooks/query/use-settings";
import { useConfig } from "#/hooks/query/use-config";
import RepoForkedIcon from "#/icons/repo-forked.svg?react";
import { I18nKey } from "#/i18n/declaration";

export function ConnectToProviderMessage(): React.JSX.Element {
  const { isLoading } = useSettings();
  const { data: config } = useConfig();
  const { t } = useTranslation();
  const isNative = config?.auth_mode === "native";
  const canConnectProvider =
    !isNative ||
    Object.values(config.git_connection_methods ?? {}).some(
      (methods) => methods.length > 0,
    );
  let messageKey = "HOME$CONNECT_PROVIDER_MESSAGE";
  if (isNative) {
    messageKey = canConnectProvider
      ? "HOME$CONNECT_NATIVE_PROVIDER_MESSAGE"
      : "NATIVE_GIT$NO_PROVIDERS";
  }

  return (
    <div className="flex flex-col gap-4 justify-between h-full">
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center gap-[10px]">
          <RepoForkedIcon width={24} height={24} />
          <span className="leading-5 font-bold text-base text-white">
            {t(I18nKey.COMMON$OPEN_REPOSITORY)}
          </span>
        </div>
        <p>{t(messageKey)}</p>
      </div>
      {canConnectProvider && (
        <Link
          data-testid="navigate-to-settings-button"
          to="/settings/integrations"
          className="self-start w-full"
        >
          <BrandButton
            type="button"
            variant="primary"
            isDisabled={isLoading}
            className="w-full font-semibold"
          >
            {!isLoading && t("SETTINGS$TITLE")}
            {isLoading && t("HOME$LOADING")}
          </BrandButton>
        </Link>
      )}
    </div>
  );
}
