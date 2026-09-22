import React from "react";
import { useTranslation } from "react-i18next";
import { useCreateStripeCheckoutSession } from "#/hooks/mutation/stripe/use-create-stripe-checkout-session";
import { useBalance } from "#/hooks/query/use-balance";
import { cn } from "#/utils/utils";
import CreditCardIcon from "#/icons/credit-card.svg?react";
import { SettingsInput } from "../settings/settings-input";
import { BrandButton } from "../settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { amountIsValid } from "#/utils/amount-is-valid";
import { I18nKey } from "#/i18n/declaration";
import { PoweredByStripeTag } from "./powered-by-stripe-tag";
import {
  formControlBorderClassName,
  formControlRadiusClassName,
  formControlSurfaceClassName,
} from "#/utils/form-control-classes";

const paymentFormWidthClassName = "w-full max-w-[680px]";

export function PaymentForm({ isDisabled }: { isDisabled?: boolean }) {
  const { t } = useTranslation();
  const { data: balance, isLoading } = useBalance();
  const { mutate: addBalance, isPending } = useCreateStripeCheckoutSession();

  const [buttonIsDisabled, setButtonIsDisabled] = React.useState(true);

  const billingFormAction = async (formData: FormData) => {
    const amount = formData.get("top-up-input")?.toString();

    if (amount?.trim()) {
      if (!amountIsValid(amount)) return;

      const intValue = parseInt(amount, 10);
      addBalance({ amount: intValue });
    }

    setButtonIsDisabled(true);
  };

  const handleTopUpInputChange = (value: string) => {
    setButtonIsDisabled(!amountIsValid(value));
  };

  return (
    <form
      action={billingFormAction}
      data-testid="billing-settings"
      className="flex flex-col gap-6"
    >
      <div
        className={cn(
          formControlBorderClassName,
          formControlRadiusClassName,
          formControlSurfaceClassName,
          paymentFormWidthClassName,
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
        {!isLoading && balance !== undefined && (
          <span
            data-testid="user-balance"
            className="shrink-0 text-2xl font-semibold tabular-nums tracking-tight text-foreground"
          >
            {balance === null
              ? t(I18nKey.CONVERSATION$NO_BUDGET_LIMIT)
              : `$${Number(balance).toFixed(2)}`}
          </span>
        )}
        {isLoading && <LoadingSpinner size="small" />}
      </div>

      <div className="flex flex-col gap-3">
        <SettingsInput
          testId="top-up-input"
          name="top-up-input"
          onChange={handleTopUpInputChange}
          type="number"
          label={t(I18nKey.PAYMENT$ADD_FUNDS)}
          placeholder={t(I18nKey.PAYMENT$SPECIFY_AMOUNT_USD)}
          className={paymentFormWidthClassName}
          min={10}
          max={25000}
          step={1}
          isDisabled={isDisabled}
        />

        <div
          className={cn(paymentFormWidthClassName, "flex items-center gap-2")}
        >
          <BrandButton
            variant="primary"
            type="submit"
            isDisabled={isPending || buttonIsDisabled || isDisabled}
          >
            {t(I18nKey.PAYMENT$ADD_CREDIT)}
          </BrandButton>
          {isPending && <LoadingSpinner size="small" />}
          <PoweredByStripeTag />
        </div>
      </div>
    </form>
  );
}
