import { dropdownMenuRowForegroundClassName } from "#/utils/dropdown-classes";
import { cn } from "#/utils/utils";

interface ServerStatusContextMenuIconTextProps {
  icon: React.ReactNode;
  text: string;
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void;
  testId?: string;
}

export function ServerStatusContextMenuIconText({
  icon,
  text,
  onClick,
  testId,
}: ServerStatusContextMenuIconTextProps) {
  return (
    <button
      className={cn(
        dropdownMenuRowForegroundClassName,
        "justify-between text-white",
      )}
      onClick={onClick}
      data-testid={testId}
      type="button"
    >
      {text}
      {icon}
    </button>
  );
}
