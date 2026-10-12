import { useQuery } from "@tanstack/react-query";
import { openHands } from "#/api/open-hands-axios";

export interface SlackStatus {
  /** Whether the current user has linked their Slack account. */
  connected: boolean;
}

/**
 * Whether the current user has completed the Slack install flow. Describes the
 * saved link, not the health of the Slack app. Only needed when Slack is
 * enabled for the instance — gate it via `enabled`.
 */
export function useSlackStatus(enabled = true) {
  return useQuery<SlackStatus>({
    queryKey: ["slack-status"],
    enabled,
    queryFn: async () => {
      const response = await openHands.get("/slack/status");
      return response.data;
    },
    // The row just omits its status chip when the lookup fails.
    meta: { disableToast: true },
  });
}
