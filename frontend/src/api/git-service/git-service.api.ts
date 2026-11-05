import { openHands } from "../open-hands-axios";
import { GitChange, GitChangeDiff } from "../open-hands.types";
import ConversationService from "../conversation-service/conversation-service.api";

/**
 * Git Service API - Handles all Git-related API endpoints
 */
class GitService {
  /**
   * Get git changes for a conversation
   * @param conversationId The conversation ID
   * @returns List of git changes
   */
  static async getGitChanges(conversationId: string): Promise<GitChange[]> {
    const url = `${ConversationService.getConversationUrl(conversationId)}/git/changes`;
    const { data } = await openHands.get<GitChange[]>(url, {
      headers: ConversationService.getConversationHeaders(),
    });
    return data;
  }

  /**
   * Get git change diff for a specific file
   * @param conversationId The conversation ID
   * @param path The file path
   * @returns Git change diff
   */
  static async getGitChangeDiff(
    conversationId: string,
    path: string,
  ): Promise<GitChangeDiff> {
    const url = `${ConversationService.getConversationUrl(conversationId)}/git/diff`;
    const { data } = await openHands.get<GitChangeDiff>(url, {
      params: { path },
      headers: ConversationService.getConversationHeaders(),
    });
    return data;
  }
}

export default GitService;
