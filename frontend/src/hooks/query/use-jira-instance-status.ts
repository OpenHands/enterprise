import { useQuery } from "@tanstack/react-query";
import { openHands } from "#/api/open-hands-axios";

export interface JiraInstanceStatus {
  /** Whether an admin has set up an org-visible Jira Cloud connection. */
  configured: boolean;
  /** Host of the connected Jira Cloud site; null when not configured. */
  host: string | null;
}

/**
 * Org-level Jira Cloud status: whether a connection is set up and its host.
 * Lets a member see "mention @openhands on a Jira issue" vs "ask an admin to
 * set it up". Only needed for a member in email mode — gate it via `enabled`.
 */
export function useJiraInstanceStatus(enabled = true) {
  return useQuery<JiraInstanceStatus>({
    queryKey: ["jira-instance-status"],
    enabled,
    queryFn: async () => {
      const response = await openHands.get(
        "/integration/jira/workspaces/status",
      );
      return response.data;
    },
  });
}
