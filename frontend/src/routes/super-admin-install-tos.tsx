import React from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import OpenHandsLogoWhite from "#/assets/branding/openhands-logo-white.svg?react";
import { BrandButton } from "#/components/features/settings/brand-button";
import { TOSCheckbox } from "#/components/features/waitlist/tos-checkbox";
import { I18nKey } from "#/i18n/declaration";
import { markSuperAdminNuxTosDone } from "#/utils/org/super-admin-nux";
import { cn } from "#/utils/utils";

/** Placeholder legal copy for the install NUX until real Enterprise TOS is wired. */
const DUMMY_TOS_UPDATED =
  "Last updated: September 23, 2026 · Placeholder draft for OpenHands Enterprise";

const DUMMY_TOS_SECTIONS: { heading: string; body: string }[] = [
  {
    heading: "1. Acceptance of Terms",
    body: "By installing, configuring, or using OpenHands Enterprise (the “Software”), you agree to be bound by these Terms of Service. If you are accepting on behalf of an organization, you represent that you have authority to bind that organization.",
  },
  {
    heading: "2. License Grant",
    body: "Subject to these terms and your applicable license or trial agreement, we grant you a limited, non-exclusive, non-transferable right to install and use the Software solely for your internal business purposes during the licensed term.",
  },
  {
    heading: "3. Restrictions",
    body: "You may not (a) reverse engineer, decompile, or disassemble the Software except to the extent permitted by law; (b) sublicense, sell, or redistribute the Software; (c) remove proprietary notices; or (d) use the Software to develop a competing product.",
  },
  {
    heading: "4. Your Data and Content",
    body: "You retain ownership of data, code, and content you submit to the Software (“Customer Content”). You are responsible for Customer Content, including compliance with applicable laws and third-party rights. You grant us only the rights needed to operate and support the Software for you.",
  },
  {
    heading: "5. Artificial Intelligence Features",
    body: "The Software may use large language models and related services to generate suggestions, code, or actions. Outputs may be inaccurate or incomplete. You are solely responsible for reviewing and validating all outputs before use in production systems.",
  },
  {
    heading: "6. Confidentiality",
    body: "Each party agrees to protect the other’s confidential information with reasonable care and to use it only as needed to perform under these terms. Confidentiality obligations survive termination for three (3) years, or longer for trade secrets.",
  },
  {
    heading: "7. Security and Privacy",
    body: "We will maintain appropriate administrative, technical, and physical safeguards. Processing of personal data is described in the applicable privacy notice and, where required, a data processing addendum.",
  },
  {
    heading: "8. Warranties and Disclaimers",
    body: "EXCEPT AS EXPRESSLY STATED IN YOUR ORDER FORM OR LICENSE AGREEMENT, THE SOFTWARE IS PROVIDED “AS IS” WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS, IMPLIED, OR STATUTORY, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.",
  },
  {
    heading: "9. Limitation of Liability",
    body: "TO THE MAXIMUM EXTENT PERMITTED BY LAW, NEITHER PARTY WILL BE LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS, REVENUE, OR DATA, ARISING OUT OF OR RELATED TO THESE TERMS OR THE SOFTWARE.",
  },
  {
    heading: "10. Term and Termination",
    body: "These terms continue for the duration of your license or trial. Either party may terminate for material breach if not cured within thirty (30) days after notice. Upon termination, you must cease use of the Software and destroy confidential materials as required.",
  },
  {
    heading: "11. Governing Law",
    body: "These terms are governed by the laws specified in your order form or, if none, the laws of the State of California, excluding conflict-of-law rules. Venue lies in the courts located in San Francisco County, California, unless otherwise agreed in writing.",
  },
  {
    heading: "12. Contact",
    body: "Questions about these Terms of Service may be sent to legal@all-hands.dev. This document is sample placeholder text for product onboarding and is not legal advice.",
  },
];

export default function SuperAdminInstallTos() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [accepted, setAccepted] = React.useState(false);

  const onContinue = () => {
    if (!accepted) return;
    markSuperAdminNuxTosDone();
    navigate("/install/account");
  };

  return (
    <div
      className="flex w-full max-w-xl flex-col items-center gap-5 text-center"
      data-testid="super-admin-install-tos"
    >
      <OpenHandsLogoWhite
        width={68}
        height={46}
        aria-label={t(I18nKey.BRANDING$OPENHANDS_LOGO)}
      />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold text-white">
          {t(I18nKey.TOS$ACCEPT_TERMS_OF_SERVICE)}
        </h1>
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SA_NUX$TOS_DESCRIPTION)}
        </p>
      </div>

      <div
        className={cn(
          "h-64 w-full overflow-y-auto rounded-lg border border-[var(--oh-border)]",
          "bg-base-secondary px-4 py-3 text-left custom-scrollbar-always",
        )}
        data-testid="sa-nux-tos-scroll"
        // The terms box is a fixed-height scroller; it needs its own tab stop.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        role="region"
        aria-label={t(I18nKey.TOS$ACCEPT_TERMS_OF_SERVICE)}
      >
        <p className="mb-4 text-xs text-[var(--oh-muted)]">
          {DUMMY_TOS_UPDATED}
        </p>
        <div className="flex flex-col gap-4">
          {DUMMY_TOS_SECTIONS.map((section) => (
            <section key={section.heading}>
              <h2 className="mb-1 text-sm font-semibold text-white">
                {section.heading}
              </h2>
              <p className="text-sm leading-5 text-[var(--oh-muted)]">
                {section.body}
              </p>
            </section>
          ))}
        </div>
      </div>

      <div className="w-full text-left">
        <TOSCheckbox onChange={() => setAccepted((v) => !v)} />
      </div>
      <BrandButton
        type="button"
        variant="primary"
        className="w-full"
        testId="sa-nux-tos-continue"
        isDisabled={!accepted}
        onClick={onContinue}
      >
        {t(I18nKey.TOS$CONTINUE)}
      </BrandButton>
    </div>
  );
}
