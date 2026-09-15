import { IntegrationsHub } from "#/components/features/integrations-hub/integrations-hub";
import { useConfig } from "#/hooks/query/use-config";
import GitSettingsScreen from "#/routes/git-settings";

export default function PersonalIntegrationsIndex() {
  const { data: config } = useConfig();
  const hubEnabled = config?.feature_flags?.enable_integrations_hub === true;

  return hubEnabled ? <IntegrationsHub /> : <GitSettingsScreen />;
}
