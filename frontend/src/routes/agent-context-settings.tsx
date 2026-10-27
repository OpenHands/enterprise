import { useState } from "react";
import { AxiosError } from "axios";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { useSaveSettings } from "#/hooks/mutation/use-save-settings";
import { useSettings } from "#/hooks/query/use-settings";
import { I18nKey } from "#/i18n/declaration";
import { Typography } from "#/ui/typography";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import {
  formControlSwitchDescriptionClassName,
  formControlSwitchFieldClassName,
} from "#/utils/form-control-classes";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { retrieveAxiosErrorMessage } from "#/utils/retrieve-axios-error-message";
import { cn } from "#/utils/utils";

export const clientLoader = createPermissionGuard("view_llm_settings");

/**
 * Personal "Agent Context" page (SaaS): the persistent-memory toggle that used
 * to sit at the bottom of the Agent page. Saves ``enable_memory_context`` on
 * the user's settings; agent behaviour itself is configured per Agent Profile.
 */
export default function AgentContextSettingsScreen() {
  const { t } = useTranslation();
  const { mutate: saveSettings, isPending } = useSaveSettings();
  const { data: settings, isLoading } = useSettings();

  const initialMemoryEnabled = !!settings?.enable_memory_context;
  const [memoryOverride, setMemoryOverride] = useState<boolean | undefined>(
    undefined,
  );
  const isMemoryEnabled = memoryOverride ?? initialMemoryEnabled;
  const isDirty = isMemoryEnabled !== initialMemoryEnabled;

  const handleSave = () => {
    saveSettings(
      { enable_memory_context: isMemoryEnabled },
      {
        onError: (error) => {
          const message = retrieveAxiosErrorMessage(error as AxiosError);
          displayErrorToast(message || t(I18nKey.ERROR$GENERIC));
        },
        onSuccess: () => {
          displaySuccessToast(t(I18nKey.SETTINGS$SAVED));
          setMemoryOverride(undefined);
        },
      },
    );
  };

  if (isLoading) return null;

  return (
    <div
      data-testid="agent-context-settings-screen"
      className="h-full relative"
    >
      <div className="flex flex-col gap-6">
        <div className={formControlSwitchFieldClassName}>
          <SettingsSwitch
            testId="agent-context-enable-memory-context"
            isToggled={isMemoryEnabled}
            isDisabled={isPending}
            onToggle={setMemoryOverride}
          >
            {t(I18nKey.SETTINGS$ENABLE_MEMORY_CONTEXT)}
          </SettingsSwitch>
          <Typography.Paragraph
            className={cn(
              formControlSwitchDescriptionClassName,
              "text-xs leading-5 text-[var(--oh-muted)]",
            )}
          >
            {t(I18nKey.SETTINGS$ENABLE_MEMORY_CONTEXT_HELPER)}
          </Typography.Paragraph>
        </div>
      </div>

      <div className="sticky bottom-0 bg-base py-4">
        <BrandButton
          testId="agent-context-save-button"
          type="button"
          variant="primary"
          isDisabled={isPending || !isDirty}
          onClick={handleSave}
        >
          {isPending
            ? t(I18nKey.SETTINGS$SAVING)
            : t(I18nKey.SETTINGS$SAVE_CHANGES)}
        </BrandButton>
      </div>
    </div>
  );
}
