import { useState } from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useCreatePasswordUser } from "#/hooks/mutation/use-create-password-user";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import {
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

export function CreatePasswordUserModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { mutate: createPasswordUser, isPending } = useCreatePasswordUser();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [resetExisting, setResetExisting] = useState(false);

  const handleCreate = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      displayErrorToast(t(I18nKey.SETTINGS$INVALID_EMAIL_FORMAT));
      return;
    }
    if (password !== confirmPassword) {
      displayErrorToast(t(I18nKey.SUPER_ADMIN$PASSWORD_USER_PASSWORD_MISMATCH));
      return;
    }
    createPasswordUser(
      { email: trimmedEmail, password, reset: resetExisting },
      { onSuccess: () => onClose() },
    );
  };

  return (
    <OrgModal
      testId="super-admin-password-user-create"
      title={t(I18nKey.SUPER_ADMIN$PASSWORD_USER_CREATE)}
      description={t(I18nKey.SUPER_ADMIN$PASSWORD_USER_CREATE_HINT)}
      primaryButtonText={t(I18nKey.BUTTON$CREATE)}
      secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
      primaryButtonTestId="super-admin-password-user-create-submit"
      secondaryButtonTestId="super-admin-password-user-create-cancel"
      isPrimaryDisabled={!email.trim() || !password || !confirmPassword}
      isLoading={isPending}
      onPrimaryClick={handleCreate}
      onClose={onClose}
      className="w-md max-w-full"
    >
      <label className="flex flex-col gap-1 text-sm">
        {t(I18nKey.SETTINGS$USER_EMAIL)}
        <div className={cn(formControlShellClassName, "w-full")}>
          <input
            data-testid="password-user-email-input"
            type="email"
            value={email}
            placeholder={t(I18nKey.COMMON$ENTER_EMAIL_ADDRESSES)}
            onChange={(e) => setEmail(e.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        {t(I18nKey.SETTINGS$NEW_PASSWORD)}
        <div className={cn(formControlShellClassName, "w-full")}>
          <input
            data-testid="password-user-password-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
      </label>
      <label className="flex flex-col gap-1 text-sm">
        {t(I18nKey.SETTINGS$CONFIRM_NEW_PASSWORD)}
        <div className={cn(formControlShellClassName, "w-full")}>
          <input
            data-testid="password-user-confirm-password-input"
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            className={cn(formControlInlineInputClassName, "text-white")}
          />
        </div>
      </label>
      <label className="flex items-start gap-2 text-sm text-gray-300">
        <input
          data-testid="password-user-reset-existing-checkbox"
          type="checkbox"
          checked={resetExisting}
          onChange={(e) => setResetExisting(e.target.checked)}
          className="mt-1"
        />
        <span>{t(I18nKey.SUPER_ADMIN$PASSWORD_USER_RESET_EXISTING)}</span>
      </label>
    </OrgModal>
  );
}
