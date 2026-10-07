import React from "react";
import { useTranslation } from "react-i18next";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { Dropdown } from "#/ui/dropdown/dropdown";
import { I18nKey } from "#/i18n/declaration";
import { displayErrorToast } from "#/utils/custom-toast-handlers";
import { CopyInviteLinkButton } from "#/components/features/org/copy-invite-link-button";
import { useCreateSignupLink } from "#/hooks/mutation/use-create-signup-link";
import { SignupLinkRole } from "#/api/idp-service/idp.types";
import {
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";

interface MintSignupLinkModalProps {
  /** The currently selected organization, if any. `null` mints an
   * instance-wide link, unlocking the `superadmin` role option. */
  orgId: string | null;
  onClose: () => void;
}

/**
 * Super-admin-only alternative to {@link InviteOrganizationMemberModal}:
 * mints a one-time sign-up link (``POST /api/idp/signup-links``) rather
 * than sending an email invitation, so it works regardless of whether
 * email delivery is configured. The caller copies the link and shares it
 * with the invitee out of band.
 */
export function MintSignupLinkModal({
  orgId,
  onClose,
}: MintSignupLinkModalProps) {
  const { t } = useTranslation();
  const { mutate: createSignupLink, isPending } = useCreateSignupLink();
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<SignupLinkRole>("member");
  const [link, setLink] = React.useState<{
    email: string;
    url: string;
  } | null>(null);

  // Org-scoped roles (member/admin) only make sense alongside an org to
  // hold that membership in. Minting an org-independent link instead
  // offers a plain "user" account (still the `member` role under the
  // hood -- see `CreateSignupLinkParams` -- just with no `orgId`) or the
  // instance-level `superadmin` role.
  const roleOptions = (
    orgId
      ? [
          { value: "member", label: t(I18nKey.ORG$ROLE_MEMBER) },
          { value: "admin", label: t(I18nKey.ORG$ROLE_ADMIN) },
        ]
      : [
          { value: "member", label: t(I18nKey.ORG$ROLE_USER) },
          { value: "superadmin", label: t(I18nKey.ORG$ROLE_SUPERADMIN) },
        ]
  ).map((option) => ({
    ...option,
    label: option.label.charAt(0).toLocaleUpperCase() + option.label.slice(1),
  }));

  const handleSubmit = () => {
    const trimmedEmail = email.trim();
    if (!trimmedEmail) {
      displayErrorToast(t(I18nKey.SETTINGS$INVALID_EMAIL_FORMAT));
      return;
    }

    createSignupLink(
      {
        email: trimmedEmail,
        role,
        orgId: role === "superadmin" ? undefined : (orgId ?? undefined),
      },
      {
        onSuccess: (data) => setLink({ email: trimmedEmail, url: data.url }),
      },
    );
  };

  if (link) {
    return (
      <OrgModal
        testId="signup-link-result-modal"
        title={t(I18nKey.ORG$SIGNUP_LINK_CREATED)}
        description={t(I18nKey.ORG$SIGNUP_LINK_CREATED_DESCRIPTION)}
        primaryButtonText={t(I18nKey.BUTTON$CLOSE)}
        onPrimaryClick={() => onClose()}
        onClose={onClose}
        hideSecondaryButton
      >
        <div
          className="flex items-center justify-between gap-2 text-sm"
          data-testid="signup-link-result"
        >
          <span className="truncate">{link.email}</span>
          <CopyInviteLinkButton inviteUrl={link.url} />
        </div>
      </OrgModal>
    );
  }

  return (
    <OrgModal
      testId="mint-signup-link-modal"
      title={t(I18nKey.ORG$CREATE_SIGNUP_LINK)}
      description={t(I18nKey.ORG$MINT_SIGNUP_LINK_DESCRIPTION)}
      primaryButtonText={t(I18nKey.BUTTON$CREATE)}
      onPrimaryClick={handleSubmit}
      onClose={onClose}
      isLoading={isPending}
    >
      <div className={cn(formControlShellClassName, "w-full")}>
        <input
          data-testid="signup-link-email-input"
          type="email"
          value={email}
          placeholder={t(I18nKey.COMMON$ENTER_EMAIL_ADDRESSES)}
          onChange={(e) => setEmail(e.target.value)}
          className={cn(formControlInlineInputClassName, "text-white")}
        />
      </div>
      <label className="flex flex-col gap-1 text-sm">
        {t(I18nKey.ORG$INVITE_ROLE_LABEL)}
        <Dropdown
          testId="signup-link-role-dropdown"
          options={roleOptions}
          defaultValue={roleOptions[0]}
          onChange={(option) =>
            setRole((option?.value as SignupLinkRole) ?? "member")
          }
        />
      </label>
    </OrgModal>
  );
}
