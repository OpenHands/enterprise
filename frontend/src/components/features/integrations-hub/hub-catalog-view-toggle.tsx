import { Grid2x2, Rows3 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ViewModeToggle } from "#/components/shared/view-mode-toggle";
import { I18nKey } from "#/i18n/declaration";

export type HubCatalogView = "list" | "card";

interface HubCatalogViewToggleProps {
  view: HubCatalogView;
  onChange: (view: HubCatalogView) => void;
}

export function HubCatalogViewToggle({
  view,
  onChange,
}: HubCatalogViewToggleProps) {
  const { t } = useTranslation();

  return (
    <ViewModeToggle
      value={view}
      onChange={onChange}
      ariaLabel={t(I18nKey.INTEGRATIONS_HUB$VIEW_MODE)}
      testId="admin-catalog-view-toggle"
      options={[
        {
          value: "list",
          icon: Rows3,
          label: t(I18nKey.INTEGRATIONS_HUB$VIEW_LIST),
          testId: "admin-catalog-view-list",
        },
        {
          value: "card",
          icon: Grid2x2,
          label: t(I18nKey.INTEGRATIONS_HUB$VIEW_CARDS),
          testId: "admin-catalog-view-cards",
        },
      ]}
    />
  );
}
