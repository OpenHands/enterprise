import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useConfig } from "#/hooks/query/use-config";
import { useDisconnectGitProvider } from "#/hooks/mutation/use-disconnect-git-provider";
import { generateIdpLinkUrl } from "#/utils/generate-idp-link-url";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { ConfirmationModal } from "#/components/shared/modals/confirmation-modal";
import { Provider } from "#/types/settings";
import DebugStackframeDot from "#/icons/debug-stackframe-dot.svg?react";
import { Typography } from "#/ui/typography";
import { BrandButton } from "../brand-button";

interface GitProviderConnectionProps {
  provider: Provider;
  providerName: string;
  isConnected: boolean;
}

/**
 * Connection status of a git provider linked to the user's account (SaaS),
 * with a Connect button that starts the Keycloak account-linking flow or a
 * Disconnect button that unlinks it. Children render only while connected
 * (e.g. webhook managers, configure links).
 */
export function GitProviderConnection({
  provider,
  providerName,
  isConnected,
  children,
}: React.PropsWithChildren<GitProviderConnectionProps>) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { mutate: disconnectGitProvider, isPending } =
    useDisconnectGitProvider();
  const [confirmDisconnect, setConfirmDisconnect] = React.useState(false);

  const handleConnect = () => {
    window.location.href = generateIdpLinkUrl(
      provider,
      new URL(window.location.href),
      config?.auth_url,
    );
  };

  const handleDisconnect = () => {
    setConfirmDisconnect(false);
    disconnectGitProvider(provider, {
      onSuccess: () => {
        displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
      },
      onError: (error) => {
        const errorMessage = retrieveAxiosErrorMessage(error);
        displayErrorToast(errorMessage || t(I18nKey.ERROR$GENERIC));
      },
    });
  };

  return (
    <>
      <div className="flex items-center">
        <DebugStackframeDot
          className="w-6 h-6 shrink-0"
          color={isConnected ? "#BCFF8C" : "#FF684E"}
        />
        <Typography.Text
          className="text-sm text-gray-400"
          testId={`${provider}-status-text`}
        >
          {t(I18nKey.COMMON$STATUS)}:{" "}
          {isConnected
            ? t(I18nKey.STATUS$CONNECTED)
            : t(I18nKey.STATUS$NOT_CONNECTED)}
        </Typography.Text>
      </div>
      {isConnected ? (
        <>
          {children}
          <BrandButton
            testId={`disconnect-${provider}-button`}
            type="button"
            variant="secondary"
            className="w-55"
            isDisabled={isPending}
            onClick={() => setConfirmDisconnect(true)}
          >
            {t(I18nKey.BUTTON$DISCONNECT)}
          </BrandButton>
        </>
      ) : (
        <BrandButton
          testId={`connect-${provider}-button`}
          type="button"
          variant="primary"
          className="w-55"
          onClick={handleConnect}
        >
          {t(I18nKey.BUTTON$CONNECT)}
        </BrandButton>
      )}
      {confirmDisconnect && (
        <ConfirmationModal
          text={t(I18nKey.GIT_PROVIDER$DISCONNECT_CONFIRMATION, {
            provider: providerName,
          })}
          onConfirm={handleDisconnect}
          onCancel={() => setConfirmDisconnect(false)}
        />
      )}
    </>
  );
}
