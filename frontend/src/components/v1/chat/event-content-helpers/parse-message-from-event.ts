import { MessageEvent } from "#/types/v1/core";
import i18n from "#/i18n";

export const parseMessageFromEvent = (event: MessageEvent): string => {
  const message = event.llm_message;

  if (!message?.content) {
    return "";
  }

  let textContent = "";
  if (message.content) {
    if (Array.isArray(message.content)) {
      textContent = message.content
        .filter((content) => content.type === "text")
        .map((content) => content.text)
        .join("\n");
    } else if (typeof message.content === "string") {
      textContent = message.content;
    }
  }

  const hasImages =
    Array.isArray(message.content) &&
    message.content.some((content) => content.type === "image");

  if (!hasImages) {
    return textContent;
  }

  // If there are images, try to split by the augmented prompt delimiter
  const delimiter = i18n.t("CHAT_INTERFACE$AUGMENTED_PROMPT_FILES_TITLE");
  const parts = textContent.split(delimiter);

  return parts[0];
};
