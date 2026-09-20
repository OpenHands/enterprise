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
import { settingsListRowActionButtonClassName } from "#/utils/settings-list-classes";
import { BrandButton } from "../brand-button";

interface GitProviderConnectionProps {
  provider: Provider;
  providerName: string;
  isConnected: boolean;
}

/**
 * Connect / Disconnect controls for a git provider linked to the user's
 * account (SaaS). Connect starts the Keycloak account-linking flow and
 * Disconnect unlinks the provider after confirmation. Children render only
 * while connected (e.g. configure links) and sit beside the Disconnect button.
 * The connection status itself is shown by the surrounding provider card.
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
      <div className="flex items-center gap-2">
        {isConnected ? (
          <>
            {children}
            <BrandButton
              testId={`disconnect-${provider}-button`}
              type="button"
              variant="secondary"
              className={settingsListRowActionButtonClassName}
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
            className={settingsListRowActionButtonClassName}
            onClick={handleConnect}
          >
            {t(I18nKey.BUTTON$CONNECT)}
          </BrandButton>
        )}
      </div>
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
