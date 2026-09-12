import React, { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useClickOutsideElement } from "#/hooks/use-click-outside-element";
import { ContextMenu } from "#/ui/context-menu";
import { ContextMenuListItem } from "../../context-menu/context-menu-list-item";
import { I18nKey } from "#/i18n/declaration";
import { ConversationNameContextMenuIconText } from "../../conversation/conversation-name-context-menu-icon-text";

import EditIcon from "#/icons/u-edit.svg?react";
import RobotIcon from "#/icons/u-robot.svg?react";
import ToolsIcon from "#/icons/u-tools.svg?react";
import DownloadIcon from "#/icons/u-download.svg?react";
import CreditCardIcon from "#/icons/u-credit-card.svg?react";
import CloseIcon from "#/icons/u-close.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";
import { Divider } from "#/ui/divider";

interface ConversationCardContextMenuProps {
  onClose: () => void;
  onDelete?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onStop?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onEdit?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDisplayCost?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onShowAgentTools?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onShowSkills?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDownloadViaVSCode?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  onDownloadConversation?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  position?: "top" | "bottom";
}

export function ConversationCardContextMenu({
  onClose,
  onDelete,
  onStop,
  onEdit,
  onDisplayCost,
  onShowAgentTools,
  onShowSkills,
  onDownloadViaVSCode,
  onDownloadConversation,
  position = "bottom",
}: ConversationCardContextMenuProps) {
  const { t } = useTranslation();
  const ref = useClickOutsideElement<HTMLUListElement>(onClose);

  const generateSection = useCallback(
    (items: React.ReactNode[], sectionKey: string, isLast?: boolean) => {
      const filteredItems = items.filter((i) => i != null);

      if (filteredItems.length > 0) {
        return !isLast ? (
          <React.Fragment key={sectionKey}>
            {filteredItems}
            <Divider />
          </React.Fragment>
        ) : (
          <React.Fragment key={sectionKey}>{filteredItems}</React.Fragment>
        );
      }
      return null;
    },
    [],
  );

  return (
    <ContextMenu
      ref={ref}
      testId="context-menu"
      position={position}
      alignment="right"
      className="mt-0"
    >
      {generateSection(
        [
          onEdit && (
            <ContextMenuListItem
              key="edit-button"
              testId="edit-button"
              onClick={onEdit}
            >
              <ConversationNameContextMenuIconText
                icon={<EditIcon width={16} height={16} />}
                text={t(I18nKey.BUTTON$RENAME)}
              />
            </ContextMenuListItem>
          ),
        ],
        "edit-section",
      )}
      {generateSection(
        [
          onShowAgentTools && (
            <ContextMenuListItem
              key="show-agent-tools-button"
              testId="show-agent-tools-button"
              onClick={onShowAgentTools}
            >
              <ConversationNameContextMenuIconText
                icon={<ToolsIcon width={16} height={16} />}
                text={t(I18nKey.BUTTON$SHOW_AGENT_TOOLS_AND_METADATA)}
              />
            </ContextMenuListItem>
          ),
          onShowSkills && (
            <ContextMenuListItem
              key="show-skills-button"
              testId="show-skills-button"
              onClick={onShowSkills}
            >
              <ConversationNameContextMenuIconText
                icon={<RobotIcon width={16} height={16} />}
                text={t(I18nKey.CONVERSATION$SHOW_SKILLS)}
              />
            </ContextMenuListItem>
          ),
        ],
        "tools-section",
      )}
      {generateSection(
        [
          onStop && (
            <ContextMenuListItem
              key="stop-button"
              testId="stop-button"
              onClick={onStop}
            >
              <ConversationNameContextMenuIconText
                icon={<CloseIcon width={16} height={16} />}
                text={t(I18nKey.COMMON$CLOSE_CONVERSATION_STOP_RUNTIME)}
              />
            </ContextMenuListItem>
          ),
          onDownloadViaVSCode && (
            <ContextMenuListItem
              key="download-vscode-button"
              testId="download-vscode-button"
              onClick={onDownloadViaVSCode}
            >
              <ConversationNameContextMenuIconText
                icon={<DownloadIcon width={16} height={16} />}
                text={t(I18nKey.BUTTON$DOWNLOAD_VIA_VSCODE)}
              />
            </ContextMenuListItem>
          ),
          onDownloadConversation && (
            <ContextMenuListItem
              key="download-trajectory-button"
              testId="download-trajectory-button"
              onClick={onDownloadConversation}
            >
              <ConversationNameContextMenuIconText
                icon={<DownloadIcon width={16} height={16} />}
                text={t(I18nKey.BUTTON$EXPORT_CONVERSATION)}
              />
            </ContextMenuListItem>
          ),
        ],
        "control-section",
      )}
      {generateSection(
        [
          onDisplayCost && (
            <ContextMenuListItem
              key="display-cost-button"
              testId="display-cost-button"
              onClick={onDisplayCost}
            >
              <ConversationNameContextMenuIconText
                icon={<CreditCardIcon width={16} height={16} />}
                text={t(I18nKey.BUTTON$DISPLAY_COST)}
              />
            </ContextMenuListItem>
          ),
          onDelete && (
            <ContextMenuListItem
              key="delete-button"
              testId="delete-button"
              onClick={onDelete}
            >
              <ConversationNameContextMenuIconText
                icon={<DeleteIcon width={16} height={16} />}
                text={t(I18nKey.COMMON$DELETE_CONVERSATION)}
              />
            </ContextMenuListItem>
          ),
        ],
        "info-section",
        true,
      )}
    </ContextMenu>
  );
}
