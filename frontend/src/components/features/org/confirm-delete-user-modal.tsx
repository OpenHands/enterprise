import { Trans, useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { I18nKey } from "#/i18n/declaration";

interface ConfirmDeleteUserModalProps {
  onConfirm: () => void;
  onCancel: () => void;
  userEmail: string;
  isLoading?: boolean;
}

/**
 * Confirms the instance-wide "Remove" action in the "All Users" view
 * (``DELETE /api/admin/users/{user_id}``) -- unlike
 * ``ConfirmRemoveMemberModal``, which only removes one org membership, this
 * permanently deletes the account itself, so the warning is stronger.
 */
export function ConfirmDeleteUserModal({
  onConfirm,
  onCancel,
  userEmail,
  isLoading = false,
}: ConfirmDeleteUserModalProps) {
  const { t } = useTranslation();

  const confirmationMessage = (
    <Trans
      i18nKey={I18nKey.ORG$DELETE_USER_WARNING}
      values={{ email: userEmail }}
      components={{ email: <span className="text-white" /> }}
    />
  );

  return (
    <OrgModal
      title={t(I18nKey.ORG$CONFIRM_DELETE_USER)}
      description={confirmationMessage}
      primaryButtonText={t(I18nKey.BUTTON$CONFIRM)}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      onPrimaryClick={onConfirm}
      onClose={onCancel}
      isLoading={isLoading}
      primaryButtonTestId="confirm-button"
      secondaryButtonTestId="cancel-button"
    />
  );
}
