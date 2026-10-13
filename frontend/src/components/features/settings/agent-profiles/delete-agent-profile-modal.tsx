import { useTranslation } from "react-i18next";
import { AgentProfileSummary } from "#/api/agent-profiles-service/agent-profiles-service.api";
import { ApiKeyModalBase } from "#/components/features/settings/api-key-modal-base";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { useDeleteAgentProfile } from "#/hooks/mutation/use-delete-agent-profile";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { mutateWithToast } from "#/utils/mutate-with-toast";

interface DeleteAgentProfileModalProps {
  profile: AgentProfileSummary | null;
  onClose: () => void;
}

export function DeleteAgentProfileModal({
  profile,
  onClose,
}: DeleteAgentProfileModalProps) {
  const { t } = useTranslation();
  const deleteProfile = useDeleteAgentProfile();

  if (!profile) return null;

  const handleDelete = async () => {
    const ok = await mutateWithToast(
      deleteProfile,
      { name: profile.name, scope: profile.scope },
      {
        success: t(I18nKey.SETTINGS$PROFILE_DELETED, { name: profile.name }),
        error: (err) => extractErrorMessage(err, t(I18nKey.ERROR$GENERIC)),
      },
    ).catch(() => null);
    if (ok !== null) onClose();
  };

  const footer = (
    <>
      <BrandButton
        type="button"
        variant="secondary"
        onClick={onClose}
        isDisabled={deleteProfile.isPending}
      >
        {t(I18nKey.BUTTON$CANCEL)}
      </BrandButton>
      <BrandButton
        testId="delete-agent-profile-confirm"
        type="button"
        variant="danger"
        onClick={handleDelete}
        isDisabled={deleteProfile.isPending}
      >
        {deleteProfile.isPending ? (
          <LoadingSpinner size="small" />
        ) : (
          t(I18nKey.BUTTON$DELETE)
        )}
      </BrandButton>
    </>
  );

  return (
    <ApiKeyModalBase
      isOpen
      title={t(I18nKey.SETTINGS$PROFILE_DELETE_TITLE)}
      footer={footer}
    >
      <Typography.Paragraph className="text-sm break-all">
        {t(I18nKey.SETTINGS$PROFILE_DELETE_CONFIRMATION, {
          name: profile.name,
        })}
      </Typography.Paragraph>
    </ApiKeyModalBase>
  );
}
