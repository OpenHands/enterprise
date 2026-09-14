import React from "react";
import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import { Budgets } from "#/components/features/budgets/budgets";

export default function BudgetsPage(): React.JSX.Element | null {
  const { enabled, isLoading } = useLiteLlmIntegration();
  const { t } = useTranslation();
  if (isLoading) return null;
  if (!enabled) return <p>{t(I18nKey.BUDGETS$LITELLM_REQUIRED)}</p>;
  return <Budgets />;
}
