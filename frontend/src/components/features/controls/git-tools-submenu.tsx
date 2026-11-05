import { useTranslation } from "react-i18next";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "../context-menu/context-menu-list-item";
import { ToolsContextMenuIconText } from "./tools-context-menu-icon-text";
import { useActiveConversation } from "#/hooks/query/use-active-conversation";
import { useTrackCreatePrButtonClicked } from "#/hooks/mutation/use-track-create-pr-button-clicked";
import { Provider } from "#/types/settings";
import {
  getGitPullPrompt,
  getGitPushPrompt,
  getCreatePRPrompt,
  getCreateNewBranchPrompt,
} from "#/utils/utils";
import { useConversationStore } from "#/stores/conversation-store";

import ArrowUpIcon from "#/icons/u-arrow-up.svg?react";
import ArrowDownIcon from "#/icons/u-arrow-down.svg?react";
import PrIcon from "#/icons/u-pr.svg?react";
import CodeBranchIcon from "#/icons/u-code-branch.svg?react";
import { I18nKey } from "#/i18n/declaration";

interface GitToolsSubmenuProps {
  onClose: () => void;
}

export function GitToolsSubmenu({ onClose }: GitToolsSubmenuProps) {
  const { t } = useTranslation();
  const { setMessageToSend } = useConversationStore();
  const { data: conversation } = useActiveConversation();
  const { mutate: trackCreatePrButtonClicked } =
    useTrackCreatePrButtonClicked();

  const currentGitProvider = conversation?.git_provider as Provider;

  const onGitPull = () => {
    setMessageToSend(getGitPullPrompt());
    onClose();
  };

  const onGitPush = () => {
    setMessageToSend(getGitPushPrompt(currentGitProvider));
    onClose();
  };

  const onCreatePR = () => {
    trackCreatePrButtonClicked(currentGitProvider ?? null);
    setMessageToSend(getCreatePRPrompt(currentGitProvider));
    onClose();
  };

  const onCreateNewBranch = () => {
    setMessageToSend(getCreateNewBranchPrompt());
    onClose();
  };

  return (
    <ContextMenu testId="git-tools-submenu" className="w-max">
      <ContextMenuListItem testId="git-pull-button" onClick={onGitPull}>
        <ToolsContextMenuIconText
          icon={<ArrowDownIcon width={16} height={16} />}
          text={t(I18nKey.COMMON$GIT_PULL)}
        />
      </ContextMenuListItem>

      <ContextMenuListItem testId="git-push-button" onClick={onGitPush}>
        <ToolsContextMenuIconText
          icon={<ArrowUpIcon width={16} height={16} />}
          text={t(I18nKey.COMMON$GIT_PUSH)}
        />
      </ContextMenuListItem>

      <ContextMenuListItem testId="create-pr-button" onClick={onCreatePR}>
        <ToolsContextMenuIconText
          icon={<PrIcon width={16} height={16} />}
          text={t(I18nKey.COMMON$CREATE_PR)}
        />
      </ContextMenuListItem>

      <ContextMenuListItem
        testId="create-new-branch-button"
        onClick={onCreateNewBranch}
      >
        <ToolsContextMenuIconText
          icon={<CodeBranchIcon width={16} height={16} />}
          text={t(I18nKey.COMMON$CREATE_NEW_BRANCH)}
        />
      </ContextMenuListItem>
    </ContextMenu>
  );
}
