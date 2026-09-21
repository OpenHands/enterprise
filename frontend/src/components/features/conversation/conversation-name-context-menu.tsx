import { useTranslation } from "react-i18next";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import { useBreakpoint } from "#/hooks/use-breakpoint";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "../context-menu/context-menu-list-item";
import { Divider } from "#/ui/divider";
import { I18nKey } from "#/i18n/declaration";
import { useActiveConversation } from "#/hooks/query/use-active-conversation";
import { useConfig } from "#/hooks/query/use-config";

import EditIcon from "#/icons/u-edit.svg?react";
import RobotIcon from "#/icons/u-robot.svg?react";
import ToolsIcon from "#/icons/u-tools.svg?react";
import DownloadIcon from "#/icons/u-download.svg?react";
import CreditCardIcon from "#/icons/u-credit-card.svg?react";
import CloseIcon from "#/icons/u-close.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";
import LinkIcon from "#/icons/link-external.svg?react";
import CopyIcon from "#/icons/copy.svg?react";
import { ConversationNameContextMenuIconText } from "./conversation-name-context-menu-icon-text";

interface ConversationNameContextMenuProps {
  onClose: () => void;
  onRename?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDelete?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onStop?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDisplayCost?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onShowAgentTools?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onShowSkills?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onShowHooks?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onTogglePublic?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDownloadConversation?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onCopyShareLink?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  shareUrl?: string;
  position?: "top" | "bottom";
}

export function ConversationNameContextMenu({
  onClose,
  onRename,
  onDelete,
  onStop,
  onDisplayCost,
  onShowAgentTools,
  onShowSkills,
  onShowHooks,
  onTogglePublic,
  onDownloadConversation,
  onCopyShareLink,
  shareUrl,
  position = "bottom",
}: ConversationNameContextMenuProps) {
  const isMobile = useBreakpoint();

  const { t } = useTranslation();
  const ref = useClickOutsideElement<HTMLUListElement>(onClose);
  const { data: conversation } = useActiveConversation();
  const { data: config } = useConfig();
  const shouldShowPublicSharing = config?.app_mode === "saas" && onTogglePublic;
  const hasTools = Boolean(onShowAgentTools || onShowSkills || onShowHooks);
  const hasInfo = Boolean(onDisplayCost);
  const hasControl = Boolean(onStop || onDelete);

  return (
    <ContextMenu
      ref={ref}
      testId="conversation-name-context-menu"
      position={position}
      alignment="left"
      className={isMobile ? "right-0 translate-x-[34%] left-auto" : ""}
    >
      {onRename && (
        <ContextMenuListItem testId="rename-button" onClick={onRename}>
          <ConversationNameContextMenuIconText
            icon={<EditIcon width={16} height={16} />}
            text={t(I18nKey.BUTTON$RENAME)}
          />
        </ContextMenuListItem>
      )}

      {hasTools && <Divider testId="separator-tools" />}

      {onShowSkills && (
        <ContextMenuListItem testId="show-skills-button" onClick={onShowSkills}>
          <ConversationNameContextMenuIconText
            icon={<RobotIcon width={16} height={16} />}
            text={t(I18nKey.CONVERSATION$SHOW_SKILLS)}
          />
        </ContextMenuListItem>
      )}

      {onShowHooks && (
        <ContextMenuListItem testId="show-hooks-button" onClick={onShowHooks}>
          <ConversationNameContextMenuIconText
            icon={<ToolsIcon width={16} height={16} />}
            text={t(I18nKey.CONVERSATION$SHOW_HOOKS)}
          />
        </ContextMenuListItem>
      )}

      {onShowAgentTools && (
        <ContextMenuListItem
          testId="show-agent-tools-button"
          onClick={onShowAgentTools}
        >
          <ConversationNameContextMenuIconText
            icon={<ToolsIcon width={16} height={16} />}
            text={t(I18nKey.BUTTON$SHOW_AGENT_TOOLS_AND_METADATA)}
          />
        </ContextMenuListItem>
      )}

      {onDownloadConversation && (
        <ContextMenuListItem
          testId="download-trajectory-button"
          onClick={onDownloadConversation}
        >
          <ConversationNameContextMenuIconText
            icon={<DownloadIcon width={16} height={16} />}
            text={t(I18nKey.BUTTON$EXPORT_CONVERSATION)}
          />
        </ContextMenuListItem>
      )}

      {(hasInfo || hasControl) && <Divider testId="separator-info-control" />}

      {onDisplayCost && (
        <ContextMenuListItem
          testId="display-cost-button"
          onClick={onDisplayCost}
        >
          <ConversationNameContextMenuIconText
            icon={<CreditCardIcon width={16} height={16} />}
            text={t(I18nKey.BUTTON$DISPLAY_COST)}
          />
        </ContextMenuListItem>
      )}

      {shouldShowPublicSharing && (
        <ContextMenuListItem
          testId="share-publicly-button"
          onClick={onTogglePublic}
        >
          <div className="flex w-full items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={conversation?.public || false}
                className="h-4 w-4 cursor-pointer"
              />
              <span>{t(I18nKey.CONVERSATION$SHARE_PUBLICLY)}</span>
            </div>
            {conversation?.public && shareUrl && onCopyShareLink && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  data-testid="copy-share-link-button"
                  onClick={onCopyShareLink}
                  className="cursor-pointer rounded p-1 hover:bg-[var(--oh-interactive-hover)]"
                  title={t(I18nKey.BUTTON$COPY_TO_CLIPBOARD)}
                >
                  <CopyIcon width={16} height={16} />
                </button>
                <a
                  data-testid="open-share-link-button"
                  href={shareUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="cursor-pointer rounded p-1 hover:bg-[var(--oh-interactive-hover)]"
                  title={t(I18nKey.BUTTON$OPEN_IN_NEW_TAB)}
                >
                  <LinkIcon width={16} height={16} />
                </a>
              </div>
            )}
          </div>
        </ContextMenuListItem>
      )}

      {onStop && (
        <ContextMenuListItem testId="stop-button" onClick={onStop}>
          <ConversationNameContextMenuIconText
            icon={<CloseIcon width={16} height={16} />}
            text={t(I18nKey.COMMON$CLOSE_CONVERSATION_STOP_RUNTIME)}
          />
        </ContextMenuListItem>
      )}

      {onDelete && (
        <ContextMenuListItem testId="delete-button" onClick={onDelete}>
          <ConversationNameContextMenuIconText
            icon={<DeleteIcon width={16} height={16} />}
            text={t(I18nKey.COMMON$DELETE_CONVERSATION)}
          />
        </ContextMenuListItem>
      )}
    </ContextMenu>
  );
}
