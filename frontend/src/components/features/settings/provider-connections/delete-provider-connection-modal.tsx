import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { ApiKeyModalBase } from "#/components/features/settings/api-key-modal-base";
import type { ProviderConnection } from "#/api/organization-service/org-provider-connections-service.api";
import { useDeleteProviderConnection } from "#/hooks/mutation/use-provider-connection-mutations";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";

interface DeleteProviderConnectionModalProps {
  orgId: string | null | undefined;
  connection: ProviderConnection | null;
  /**
   * Profiles still linked to the connection. The backend refuses the delete
   * (409) while any remain, so the dialog says so up front and disables
   * Delete instead of letting the admin confirm into a server error.
   */
  linkedProfileCount: number;
  onClose: () => void;
}

export function DeleteProviderConnectionModal({
  orgId,
  connection,
  linkedProfileCount,
  onClose,
}: DeleteProviderConnectionModalProps) {
  const { t } = useTranslation();
  const deleteConnection = useDeleteProviderConnection(orgId);

  if (!connection) return null;

  const isBlocked = linkedProfileCount > 0;

  const handleDelete = async () => {
    try {
      await deleteConnection.mutateAsync(connection.id);
      displaySuccessToast(
        t(I18nKey.SETTINGS$PROVIDER_CONNECTION_DELETED, {
          name: connection.display_name,
        }),
      );
      onClose();
    } catch (error) {
      // Safety net for a profile linked after this list loaded: the backend
      // returns 409 naming the referencing profiles; surface it verbatim.
      displayErrorToast(extractErrorMessage(error, t(I18nKey.ERROR$GENERIC)));
    }
  };

  const handleClose = () => {
    if (!deleteConnection.isPending) onClose();
  };

  const footer = (
    <>
      <BrandButton
        type="button"
        variant="secondary"
        onClick={handleClose}
        isDisabled={deleteConnection.isPending}
      >
        {t(I18nKey.BUTTON$CANCEL)}
      </BrandButton>
      <BrandButton
        testId="delete-provider-connection-confirm"
        type="button"
        variant="danger"
        onClick={handleDelete}
        isDisabled={isBlocked || deleteConnection.isPending}
        aria-busy={deleteConnection.isPending}
      >
        {deleteConnection.isPending ? (
          <>
            <LoadingSpinner size="small" />
            <span className="sr-only">{t(I18nKey.BUTTON$DELETE)}</span>
          </>
        ) : (
          t(I18nKey.BUTTON$DELETE)
        )}
      </BrandButton>
    </>
  );

  return (
    <ApiKeyModalBase
      isOpen
      title={t(I18nKey.SETTINGS$PROVIDER_CONNECTION_DELETE_TITLE)}
      footer={footer}
    >
      <Typography.Paragraph className="text-sm break-all">
        {isBlocked
          ? t(I18nKey.SETTINGS$PROVIDER_CONNECTION_DELETE_BLOCKED, {
              name: connection.display_name,
              count: linkedProfileCount,
            })
          : t(I18nKey.SETTINGS$PROVIDER_CONNECTION_DELETE_CONFIRMATION, {
              name: connection.display_name,
            })}
      </Typography.Paragraph>
    </ApiKeyModalBase>
  );
}
