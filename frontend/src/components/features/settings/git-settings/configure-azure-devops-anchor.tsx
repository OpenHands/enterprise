import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useAuthCapabilities } from "#/hooks/query/use-auth-capabilities";
import { generateIdpLinkUrl } from "#/utils/generate-idp-link-url";
import { BrandButton } from "../brand-button";

export function ConfigureAzureDevOpsAnchor() {
  const { t } = useTranslation();
  const { data: capabilities } = useAuthCapabilities();

  const handleOAuthFlow = () => {
    if (!capabilities?.repository_connections.broker) {
      return;
    }

    window.location.href = generateIdpLinkUrl(
      "azure_devops",
      new URL(window.location.href),
    );
  };

  return (
    <div data-testid="configure-azure-devops-button" className="py-9">
      <BrandButton
        type="button"
        variant="primary"
        className="w-55"
        onClick={handleOAuthFlow}
      >
        {t(I18nKey.AZURE_DEVOPS$CONNECT_ACCOUNT)}
      </BrandButton>
    </div>
  );
}
