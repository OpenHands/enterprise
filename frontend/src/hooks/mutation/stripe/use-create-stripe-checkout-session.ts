import type { UseMutationResult, DefaultError } from "@tanstack/react-query";
import { useMutation } from "@tanstack/react-query";
import { useLiteLlmIntegration } from "#/hooks/use-litellm-integration";
import BillingService from "#/api/billing-service/billing-service.api";

export const useCreateStripeCheckoutSession = (): UseMutationResult<
  void,
  DefaultError,
  { amount: number },
  unknown
> => {
  const { requireLiteLlm } = useLiteLlmIntegration();
  return useMutation({
    mutationFn: async (variables: { amount: number }) => {
      requireLiteLlm();
      const redirectUrl = await BillingService.createCheckoutSession(
        variables.amount,
      );
      window.location.href = redirectUrl;
    },
  });
};
