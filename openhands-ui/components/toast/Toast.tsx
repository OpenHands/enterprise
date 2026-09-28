import { toast as sonnerToast, type ExternalToast } from "sonner";
import { Icon, type IconProps } from "../icon/Icon";
import { cn } from "../../shared/utils/cn";
import { Typography } from "../typography/Typography";
import { toastStyles } from "./utils";
import type { JSX } from "react";
import { invariant } from "../../shared/utils/invariant";

type RenderContentProps = {
  onDismiss: () => void;
};

type WithRenderContent = {
  renderContent: (props: RenderContentProps) => JSX.Element;
  text?: never;
  icon?: never;
};

type WithTextAndIcon = {
  text: string;
  icon: IconProps["icon"];
  iconClassName: string;
  textClassName?: string;
  renderContent?: never;
};

type IBaseToastProps = (WithRenderContent | WithTextAndIcon) & {
  id: string | number;
};

/**
 * Neo / agent-canvas toast chrome: tertiary surface, input border, 8px radius,
 * icon + message row (no pill shape).
 */
const BaseToast = (props: IBaseToastProps) => {
  invariant(
    !!props.renderContent || !!props.text,
    "Either define renderContent or text. Both cannot be defined."
  );

  const onDismiss = () => sonnerToast.dismiss(props.id);

  return (
    <div
      className={cn(
        "rounded-lg border border-grey-600",
        "bg-grey-800 px-3 py-2.5",
        "max-w-[400px] min-w-32",
        "flex flex-row items-center gap-x-2",
        "shadow-none",
      )}
    >
      {props.renderContent ? (
        props.renderContent({ onDismiss })
      ) : (
        <>
          <Icon
            icon={props.icon}
            className={cn("h-4 w-4 shrink-0", props.iconClassName)}
          />
          <Typography.Text
            fontSize="xs"
            className={cn(
              "min-w-0 flex-1 break-words whitespace-pre-wrap",
              props.textClassName ?? "text-white",
            )}
          >
            {props.text}
          </Typography.Text>
          <button
            type="button"
            onClick={onDismiss}
            className="cursor-pointer shrink-0"
            aria-label="Dismiss"
          >
            <Icon icon="X" className="h-4 w-4 text-light-neutral-600" />
          </button>
        </>
      )}
    </div>
  );
};

function showTypedToast(
  type: keyof typeof toastStyles,
  text?: string,
  props?: ExternalToast,
) {
  const styles = toastStyles[type];
  sonnerToast.custom(
    (id) => (
      <BaseToast
        id={id}
        icon={styles.icon}
        iconClassName={cn(styles.iconColor)}
        textClassName={styles.textColor}
        text={text!}
      />
    ),
    props,
  );
}

export const toasterMessages = {
  error: (text?: string, props?: ExternalToast) => {
    showTypedToast("error", text, props);
  },
  success: (text?: string, props?: ExternalToast) => {
    showTypedToast("success", text, props);
  },
  info: (text?: string, props?: ExternalToast) => {
    showTypedToast("info", text, props);
  },
  warning: (text?: string, props?: ExternalToast) => {
    showTypedToast("warning", text, props);
  },
  custom: (
    renderContent: WithRenderContent["renderContent"],
    props?: ExternalToast
  ) => {
    sonnerToast.custom(
      (id) => <BaseToast id={id} renderContent={renderContent} />,
      props
    );
  },
};
