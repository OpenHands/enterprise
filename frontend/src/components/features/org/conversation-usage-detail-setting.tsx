import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { organizationService } from "#/api/organization-service/organization-service.api";
import { SettingsSwitch } from "#/components/features/settings/settings-switch";
import { I18nKey } from "#/i18n/declaration";
import { Organization } from "#/types/org";

export function ConversationUsageDetailSetting({
  organization,
}: {
  organization: Organization;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { mutate, isPending } = useMutation({
    mutationFn: (enabled: boolean) =>
      organizationService.setConversationUsageDetail({
        orgId: organization.id,
        enabled,
      }),
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: ["organizations"] }),
  });

  return (
    <SettingsSwitch
      testId="conversation-usage-detail-switch"
      name="enable_conversation_usage_detail"
      isToggled={organization.enable_conversation_usage_detail === true}
      isDisabled={isPending}
      onToggle={mutate}
      hint={t(I18nKey.ORG$CONVERSATION_USAGE_DETAIL_HINT)}
    >
      {t(I18nKey.ORG$CONVERSATION_USAGE_DETAIL)}
    </SettingsSwitch>
  );
}
