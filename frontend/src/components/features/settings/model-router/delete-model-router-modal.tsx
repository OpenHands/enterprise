import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { ApiKeyModalBase } from "#/components/features/settings/api-key-modal-base";
import type { MetaProfileInfo } from "#/api/organization-service/org-model-routers-service.api";
import { useDeleteOrgModelRouter } from "#/hooks/mutation/use-org-model-router-mutations";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { extractErrorMessage } from "#/utils/extract-error-message";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";

interface DeleteModelRouterModalProps {
  orgId: string;
  router: MetaProfileInfo | null;
  onClose: () => void;
}

export function DeleteModelRouterModal({
  orgId,
  router,
  onClose,
}: DeleteModelRouterModalProps) {
  const { t } = useTranslation();
  const deleteRouter = useDeleteOrgModelRouter(orgId);

  if (!router) return null;

  const handleDelete = async () => {
    try {
      await deleteRouter.mutateAsync(router.name);
      displaySuccessToast(
        t(I18nKey.SETTINGS$MODEL_ROUTER_DELETED, { name: router.name }),
      );
      onClose();
    } catch (error) {
      displayErrorToast(extractErrorMessage(error, t(I18nKey.ERROR$GENERIC)));
    }
  };

  const handleClose = () => {
    if (!deleteRouter.isPending) onClose();
  };

  const footer = (
    <>
      <BrandButton
        type="button"
        variant="secondary"
        onClick={handleClose}
        isDisabled={deleteRouter.isPending}
      >
        {t(I18nKey.BUTTON$CANCEL)}
      </BrandButton>
      <BrandButton
        testId="delete-model-router-confirm"
        type="button"
        variant="danger"
        onClick={handleDelete}
        isDisabled={deleteRouter.isPending}
        aria-busy={deleteRouter.isPending}
      >
        {deleteRouter.isPending ? (
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
      title={t(I18nKey.SETTINGS$MODEL_ROUTER_DELETE_TITLE)}
      footer={footer}
    >
      <Typography.Paragraph className="text-sm break-all">
        {t(I18nKey.SETTINGS$MODEL_ROUTER_DELETE_CONFIRMATION, {
          name: router.name,
        })}
      </Typography.Paragraph>
    </ApiKeyModalBase>
  );
}
