import React from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { BadgeInput } from "#/components/shared/inputs/badge-input";
import { Dropdown } from "#/ui/dropdown/dropdown";
import { useAdminInviteMembers } from "#/hooks/mutation/use-admin-invite-members";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { areAllEmailsValid, hasDuplicates } from "#/utils/input-validation";
import { OrganizationUserRole } from "#/types/org";

interface AdminInviteMemberModalProps {
  orgId: string;
  orgName: string;
  onClose: () => void;
}

/**
 * Super-admin invite modal. Unlike the org-scoped invite modal this targets an
 * explicit org and allows assigning the ``owner`` role (needed to seed the
 * initial owner of an org the super admin created).
 */
export function AdminInviteMemberModal({
  orgId,
  orgName,
  onClose,
}: AdminInviteMemberModalProps) {
  const { t } = useTranslation();
  const { mutate: inviteMembers, isPending } = useAdminInviteMembers();
  const [emails, setEmails] = React.useState<string[]>([]);
  const [role, setRole] = React.useState<OrganizationUserRole>("member");

  const roleOptions = [
    { value: "member", label: t(I18nKey.ORG$ROLE_MEMBER) },
    { value: "admin", label: t(I18nKey.ORG$ROLE_ADMIN) },
    { value: "owner", label: t(I18nKey.ORG$ROLE_OWNER) },
  ];

  const handleSubmit = () => {
    const trimmed = emails.map((email) => email.trim()).filter(Boolean);

    if (trimmed.length === 0) {
      displayErrorToast(t(I18nKey.ORG$NO_EMAILS_ADDED_HINT));
      return;
    }
    if (!areAllEmailsValid(trimmed)) {
      displayErrorToast(t(I18nKey.SETTINGS$INVALID_EMAIL_FORMAT));
      return;
    }
    if (hasDuplicates(trimmed)) {
      displayErrorToast(t(I18nKey.ORG$DUPLICATE_EMAILS_ERROR));
      return;
    }

    inviteMembers(
      { orgId, emails: trimmed, role },
      { onSuccess: () => onClose() },
    );
  };

  return (
    <OrgModal
      testId="admin-invite-member-modal"
      title={t(I18nKey.SUPER_ADMIN$INVITE_TO_ORG_TITLE)}
      description={orgName}
      primaryButtonText={t(I18nKey.ORG$INVITE_ORG_MEMBERS)}
      onPrimaryClick={handleSubmit}
      onClose={onClose}
      isLoading={isPending}
    >
      <div className="flex flex-col gap-3 w-full">
        <BadgeInput
          name="admin-invite-emails"
          value={emails}
          placeholder={t(I18nKey.COMMON$ENTER_EMAIL_ADDRESSES)}
          onChange={setEmails}
        />
        <Dropdown
          testId="admin-invite-role-dropdown"
          options={roleOptions}
          defaultValue={roleOptions[0]}
          onChange={(option) =>
            setRole((option?.value as OrganizationUserRole) ?? "member")
          }
        />
      </div>
    </OrgModal>
  );
}
