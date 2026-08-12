import React from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useMe } from "#/hooks/query/use-me";
import { useBalance } from "#/hooks/query/use-balance";
import { useConfig } from "#/hooks/query/use-config";
import { usePermission } from "#/hooks/organizations/use-permissions";
import { createPermissionGuard } from "#/utils/org/permission-guard";
import { isBillingHidden } from "#/utils/org/billing-visibility";
import { AddCreditsModal } from "#/components/features/org/add-credits-modal";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import CreditCardIcon from "#/icons/credit-card.svg?react";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { cn } from "#/utils/utils";
import {
  formControlBorderClassName,
  formControlRadiusClassName,
  formControlSurfaceClassName,
} from "#/utils/form-control-classes";

export const clientLoader = createPermissionGuard("view_billing");

const creditsModuleWidthClassName = "w-full max-w-[680px]";

function CreditsSettingsScreen() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: me } = useMe();
  const { data: balance, isLoading } = useBalance();
  const { data: config } = useConfig();
  const { hasPermission } = usePermission(me?.role ?? "member");

  const [addCreditsFormVisible, setAddCreditsFormVisible] =
    React.useState(false);
  const hasHandledCheckoutRef = React.useRef(false);

  const canAddCredits = !!me && hasPermission("add_credits");
  const shouldHideBilling = isBillingHidden(
    config,
    hasPermission("view_billing"),
  );
  const checkoutStatus = searchParams.get("checkout");

  React.useEffect(() => {
    if (!checkoutStatus) return;
    if (hasHandledCheckoutRef.current) return;
    hasHandledCheckoutRef.current = true;

    if (checkoutStatus === "success") {
      displaySuccessToast(t(I18nKey.PAYMENT$SUCCESS));
      setSearchParams({});
    } else if (checkoutStatus === "cancel") {
      displayErrorToast(t(I18nKey.PAYMENT$CANCELLED));
      setSearchParams({});
    }
  }, [checkoutStatus, setSearchParams, t]);

  if (shouldHideBilling) {
    return null;
  }

  return (
    <div
      data-testid="org-credits"
      className="flex w-full flex-col items-start gap-6"
    >
      <div
        className={cn(
          formControlBorderClassName,
          formControlRadiusClassName,
          formControlSurfaceClassName,
          creditsModuleWidthClassName,
          "flex items-center justify-between gap-4 px-4 py-4",
        )}
      >
        <div className="flex min-w-0 items-center gap-3">
          <span
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/15 text-primary"
            aria-hidden
          >
            <CreditCardIcon width={18} height={18} />
          </span>
          <span className="truncate text-sm font-medium text-muted">
            {t(I18nKey.PAYMENT$AVAILABLE_CREDITS)}
          </span>
        </div>
        {!isLoading && (
          <span
            data-testid="available-credits"
            className="shrink-0 text-2xl font-semibold tabular-nums tracking-tight text-foreground"
          >
            ${Number(balance ?? 0).toFixed(2)}
          </span>
        )}
        {isLoading && <LoadingSpinner size="small" />}
      </div>

      {canAddCredits && (
        <BrandButton
          type="button"
          variant="primary"
          onClick={() => setAddCreditsFormVisible(true)}
        >
          {t(I18nKey.ORG$ADD)}
        </BrandButton>
      )}

      {addCreditsFormVisible && (
        <AddCreditsModal onClose={() => setAddCreditsFormVisible(false)} />
      )}
    </div>
  );
}

export default CreditsSettingsScreen;
