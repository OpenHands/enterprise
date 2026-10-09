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

/** Sentinel `Dropdown` option value for "no organization" -- `Dropdown`
 * options require a string value, and `null`/`undefined` aren't valid org
 * ids anyway, so a real org id can never collide with this. */
const NO_ORGANIZATION_OPTION_VALUE = "__none__";

interface MintSignupLinkModalProps {
  /** The organization to scope the link to, if any. `null` mints an
   * instance-wide link, unlocking the `superadmin` role option. Only used
   * as the initial selection when `organizations` is given -- otherwise
   * it's the fixed, non-editable scope (e.g. the per-org members page). */
  orgId: string | null;
  onClose: () => void;
  /**
   * Lets the caller pick which org (if any) to scope the link to, via a
   * dropdown, instead of a fixed `orgId`. Used by the Super Admin Users
   * page, which has no single org in context. Omit to keep `orgId` fixed.
   */
  organizations?: { id: string; name: string }[];
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
  organizations,
}: MintSignupLinkModalProps) {
  const { t } = useTranslation();
  const { mutate: createSignupLink, isPending } = useCreateSignupLink();
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<SignupLinkRole>("member");
  const [selectedOrgId, setSelectedOrgId] = React.useState(orgId);
  const [link, setLink] = React.useState<{
    email: string;
    url: string;
  } | null>(null);

  // Without an `organizations` dropdown, `orgId` is the fixed scope the
  // caller decided on; with one, the admin's own selection takes over.
  const effectiveOrgId = organizations ? selectedOrgId : orgId;
  const hasOrg = !!effectiveOrgId;

  const orgDropdownOptions = organizations && [
    {
      value: NO_ORGANIZATION_OPTION_VALUE,
      label: t(I18nKey.ORG$NO_ORGANIZATION),
    },
    ...organizations.map((org) => ({ value: org.id, label: org.name })),
  ];
  const defaultOrgOption =
    orgDropdownOptions?.find(
      (option) => option.value === (orgId ?? NO_ORGANIZATION_OPTION_VALUE),
    ) ?? orgDropdownOptions?.[0];

  // Org-scoped roles (member/admin) only make sense alongside an org to
  // hold that membership in. Minting an org-independent link instead
  // offers a plain "user" account (still the `member` role under the
  // hood -- see `CreateSignupLinkParams` -- just with no `orgId`) or the
  // instance-level `superadmin` role.
  const roleOptions = (
    hasOrg
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

  // The role options (and which ones are valid) depend on whether an org is
  // selected -- reset to the new list's default instead of keeping a role
  // (e.g. "superadmin") that no longer applies once an org is picked.
  React.useEffect(() => {
    setRole("member");
  }, [hasOrg]);

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
        orgId:
          role === "superadmin" ? undefined : (effectiveOrgId ?? undefined),
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
      {orgDropdownOptions && (
        <label className="flex flex-col gap-1 text-sm">
          {t(I18nKey.COMMON$ORGANIZATION)}
          <Dropdown
            testId="signup-link-org-dropdown"
            options={orgDropdownOptions}
            defaultValue={defaultOrgOption}
            onChange={(option) =>
              setSelectedOrgId(
                option && option.value !== NO_ORGANIZATION_OPTION_VALUE
                  ? option.value
                  : null,
              )
            }
          />
        </label>
      )}
      <label className="flex flex-col gap-1 text-sm">
        {t(I18nKey.ORG$INVITE_ROLE_LABEL)}
        <Dropdown
          // Remount when the role list itself changes (org vs no-org) so
          // the displayed selection resets to the new `defaultValue`
          // instead of keeping a stale, now-invalid one (e.g. "superadmin"
          // after an org is picked).
          key={hasOrg ? "org-role" : "no-org-role"}
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
