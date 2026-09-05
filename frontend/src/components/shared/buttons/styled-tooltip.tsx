import { Tooltip } from "@heroui/react";
import React, { ReactNode } from "react";
import { cn } from "#/utils/utils";

export interface StyledTooltipProps {
  children: ReactNode;
  content: string | ReactNode;
  tooltipClassName?: React.HTMLAttributes<HTMLDivElement>["className"];
  placement?: React.ComponentProps<typeof Tooltip.Content>["placement"];
  showArrow?: boolean;
  closeDelay?: number;
}

export function StyledTooltip({
  children,
  content,
  tooltipClassName,
  placement = "right",
  showArrow = false,
  closeDelay = 100,
}: StyledTooltipProps) {
  return (
    <Tooltip closeDelay={closeDelay}>
      <Tooltip.Trigger>
        <div className="inline-flex">{children}</div>
      </Tooltip.Trigger>
      <Tooltip.Content
        showArrow={showArrow}
        placement={placement}
        className={cn("bg-white text-black", tooltipClassName)}
      >
        {content}
      </Tooltip.Content>
    </Tooltip>
  );
}
