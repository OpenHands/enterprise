import { useMutation } from "@tanstack/react-query";
import SkillsService, { MarketplaceCheckResponse } from "#/api/skills-service";
import { MarketplaceRegistration } from "#/types/settings";

export const useCheckMarketplace = () =>
  useMutation({
    // A check that fails to run falls through to the save step, which reports
    // its own errors, so it must not toast here.
    meta: { disableToast: true },
    mutationFn: async (
      marketplace: MarketplaceRegistration,
    ): Promise<MarketplaceCheckResponse> =>
      SkillsService.checkMarketplace(marketplace),
  });
