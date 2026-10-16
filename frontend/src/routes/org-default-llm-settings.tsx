import React from "react";
import { useTranslation } from "react-i18next";
import { SuperAdminGroupSetupModal } from "#/components/features/super-admin/super-admin-group-setup-modal";
import { useSaveSettings } from "#/hooks/mutation/use-save-settings";
import { useMe } from "#/hooks/query/use-me";
import { useSettings } from "#/hooks/query/use-settings";
import { I18nKey } from "#/i18n/declaration";
import { ConfigurationOperation } from "#/types/settings";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { cn } from "#/utils/utils";
import InfoCircleIcon from "#/icons/info-circle.svg?react";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { LlmSettingsScreen } from "./llm-settings";

export const clientLoader = createPermissionGuard("view_llm_settings");

const operations: Array<{
  value: ConfigurationOperation;
  label: string;
  description: string;
}> = [
  {
    value: "locked",
    label: "Locked",
    description:
      "Members can choose organization-managed profiles but cannot add their own.",
  },
  {
    value: "replace",
    label: "Replace",
    description:
      "Members can use their own default instead of the organization default.",
  },
  {
    value: "subset",
    label: "Subset",
    description: "Members can choose from organization-managed profiles.",
  },
  {
    value: "extend",
    label: "Extend",
    description:
      "Members can use organization profiles and add personal profiles.",
  },
];

function OperationPicker({ operation }: { operation: ConfigurationOperation }) {
  const { t } = useTranslation();
  const saveSettings = useSaveSettings("org");

  const updateOperation = async (next: ConfigurationOperation) => {
    try {
      await saveSettings.mutateAsync({
        configuration_policy: { llm: { operation: next } },
      });
      displaySuccessToast("Member LLM permissions updated");
    } catch (error) {
      displayErrorToast(
        error instanceof Error ? error.message : "Unable to update permissions",
      );
    }
  };

  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
      <h2 className="text-base font-semibold text-white">
        {t(I18nKey.SETTINGS$ORG_WIDE_SETTING_BADGE)}
      </h2>
      <p className="mt-1 text-sm text-[var(--oh-muted)]">
        {t(I18nKey.SETTINGS$ORG_DEFAULTS_INFO)}
      </p>
      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        {operations.map((option) => (
          <button
            key={option.value}
            type="button"
            disabled={saveSettings.isPending}
            aria-pressed={operation === option.value}
            onClick={() => updateOperation(option.value)}
            className={cn(
              "rounded-lg border p-3 text-left transition-colors",
              operation === option.value
                ? "border-primary bg-primary/10"
                : "border-white/10 hover:border-white/30",
            )}
          >
            <span className="block text-sm font-semibold text-white">
              {option.label}
            </span>
            <span className="mt-1 block text-xs text-[var(--oh-muted)]">
              {option.description}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function TabButton({
  active,
  children,
  onClick,
}: React.PropsWithChildren<{ active: boolean; onClick: () => void }>) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "border-b-2 px-4 py-2 text-sm font-medium",
        active
          ? "border-primary text-white"
          : "border-transparent text-[var(--oh-muted)] hover:text-white",
      )}
    >
      {children}
    </button>
  );
}

function LockedMemberNotice() {
  const { t } = useTranslation();

  return (
    <div
      data-testid="locked-member-notice"
      role="note"
      className="flex items-start gap-4 rounded-xl border border-blue-400/60 bg-blue-500/10 px-5 py-5 text-blue-200"
    >
      <InfoCircleIcon
        width={24}
        height={24}
        className="mt-0.5 shrink-0 text-blue-300"
        aria-hidden="true"
      />
      <p className="text-sm leading-6 !text-[#b8d7ff] sm:text-base">
        {t(I18nKey.SETTINGS$LOCKED_LLM_NOTICE)}
      </p>
    </div>
  );
}

function OrgDefaultLlmSettingsScreen() {
  const { t } = useTranslation();
  const { data: me } = useMe();
  const { data: orgSettings } = useSettings("org");
  const [adminView, setAdminView] = React.useState<"org" | "personal">("org");
  const isAdmin = me?.role === "admin" || me?.role === "owner";
  const operation =
    orgSettings?.configuration_policy?.llm.operation ?? "extend";

  let content: React.ReactNode;
  if (isAdmin) {
    content = (
      <div className="flex flex-col gap-6">
        <div className="flex border-b border-white/10" role="tablist">
          <TabButton
            active={adminView === "org"}
            onClick={() => setAdminView("org")}
          >
            {t(I18nKey.SETTINGS$ORG_SETTINGS_HEADER)}
          </TabButton>
          <TabButton
            active={adminView === "personal"}
            onClick={() => setAdminView("personal")}
          >
            {t(I18nKey.SETTINGS$PERSONAL_SETTINGS_HEADER)}
          </TabButton>
        </div>
        {adminView === "org" ? (
          <>
            <OperationPicker operation={operation} />
            <LlmSettingsScreen scope="org" />
          </>
        ) : (
          <LlmSettingsScreen scope="personal" />
        )}
      </div>
    );
  } else if (operation === "locked") {
    content = (
      <div className="flex flex-col gap-8">
        <LockedMemberNotice />
        <LlmSettingsScreen
          scope="org"
          canActivateOrgProfiles
          showOrgManagedBadges
          showReadOnlyProviderConnections
        />
      </div>
    );
  } else if (operation === "subset") {
    content = <LlmSettingsScreen scope="org" canActivateOrgProfiles />;
  } else if (operation === "extend") {
    content = (
      <div className="flex flex-col gap-8">
        <section className="flex flex-col gap-4">
          <div>
            <h2 className="text-base font-semibold text-white">
              {t(I18nKey.SETTINGS$ORG_SETTINGS_HEADER)}
            </h2>
            <p className="text-sm text-[var(--oh-muted)]">
              {t(I18nKey.SETTINGS$ORG_DEFAULTS_INFO)}
            </p>
          </div>
          <LlmSettingsScreen scope="org" canActivateOrgProfiles />
        </section>
        <section className="flex flex-col gap-4">
          <h2 className="text-base font-semibold text-white">
            {t(I18nKey.SETTINGS$PERSONAL_SETTINGS_HEADER)}
          </h2>
          <LlmSettingsScreen scope="personal" />
        </section>
      </div>
    );
  } else {
    content = <LlmSettingsScreen scope="personal" />;
  }

  return (
    <>
      {content}
      <SuperAdminGroupSetupModal />
    </>
  );
}

export default OrgDefaultLlmSettingsScreen;
