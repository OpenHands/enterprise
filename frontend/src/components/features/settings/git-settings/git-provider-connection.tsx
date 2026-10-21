import React from "react";
import type { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useConfig } from "#/hooks/query/use-config";
import { useDisconnectGitProvider } from "#/hooks/mutation/use-disconnect-git-provider";
import { useConnectGitProvider } from "#/hooks/mutation/use-connect-git-provider";
import {
  useLinkOAuthV2GitProvider,
  useUnlinkOAuthV2GitProvider,
} from "#/hooks/mutation/use-oauth-v2-git-provider-mutations";
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
  /** `auth_providers.id`. When set, connect/disconnect use the OAuth v2
   * link/unlink routes instead of the Keycloak account-linking flow. */
  oauthProviderId?: number;
}

/**
 * Connect / Disconnect controls for a git provider linked to the user's
 * account (SaaS). Connect starts the Keycloak account-linking flow (or the
 * OAuth v2 link flow) and Disconnect unlinks the provider after confirmation.
 * Children render only while connected (e.g. configure links) and sit beside
 * the Disconnect button. The connection status itself is shown by the
 * surrounding provider card.
 */
export function GitProviderConnection({
  provider,
  providerName,
  isConnected,
  oauthProviderId,
  children,
}: React.PropsWithChildren<GitProviderConnectionProps>) {
  const { t } = useTranslation();
  const { data: config } = useConfig();
  const { mutate: disconnectGitProvider, isPending: isDisconnecting } =
    useDisconnectGitProvider();
  const { mutate: connectGitProvider, isPending: isConnecting } =
    useConnectGitProvider();
  const { mutate: linkOAuthV2, isPending: isLinking } =
    useLinkOAuthV2GitProvider();
  const { mutate: unlinkOAuthV2, isPending: isUnlinking } =
    useUnlinkOAuthV2GitProvider();
  const [confirmDisconnect, setConfirmDisconnect] = React.useState(false);

  const handleError = (error: AxiosError) => {
    displayErrorToast(
      retrieveAxiosErrorMessage(error) || t(I18nKey.ERROR$GENERIC),
    );
  };

  const handleConnect = () => {
    if (oauthProviderId !== undefined) {
      linkOAuthV2(oauthProviderId, { onError: handleError });
      return;
    }
    connectGitProvider(
      {
        provider,
        requestUrl: new URL(window.location.href),
        authUrl: config?.auth_url,
      },
      {
        onSuccess: (url) => {
          window.location.href = url;
        },
        onError: handleError,
      },
    );
  };

  const handleDisconnect = () => {
    setConfirmDisconnect(false);
    const options = {
      onSuccess: () => {
        displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
      },
      onError: handleError,
    };
    if (oauthProviderId !== undefined) {
      unlinkOAuthV2(oauthProviderId, options);
    } else {
      disconnectGitProvider(provider, options);
    }
  };
  const isPending = isDisconnecting || isUnlinking;

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
            isDisabled={isConnecting || isLinking}
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
