import { useParams } from "react-router";
import { useAppMode } from "#/hooks/use-app-mode";
import { useUserConversation } from "#/hooks/query/use-user-conversation";

const APP_TITLE_OSS = "OpenHands";
const APP_TITLE_SAAS = "OpenHands Cloud";

/**
 * Hook that returns the appropriate document title based on the deployment and current route.
 * - For conversation pages: "Conversation Title | OpenHands" or "Conversation Title | OpenHands Cloud"
 * - For other pages: "OpenHands" or "OpenHands Cloud"
 * Only the All-Hands managed cloud is "OpenHands Cloud"; OSS and self-hosted
 * enterprise installs are plain "OpenHands", matching the agent-canvas title.
 */
export const useAppTitle = () => {
  const { isOss, isEnterpriseSelfHosted } = useAppMode();
  const { conversationId } = useParams<{ conversationId: string }>();
  const { data: conversation } = useUserConversation(conversationId ?? null);

  const appTitle =
    isOss || isEnterpriseSelfHosted ? APP_TITLE_OSS : APP_TITLE_SAAS;
  const conversationTitle = conversation?.title;

  if (conversationId && conversationTitle) {
    return `${conversationTitle} | ${appTitle}`;
  }

  return appTitle;
};
