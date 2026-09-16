import type { JSX, MouseEventHandler, ReactNode } from "react";
import { cn } from "#/utils/utils";

export const loginButtonLabelClassName = "text-sm font-medium leading-5 px-1";

interface LoginButtonProps {
  className?: string;
  type?: "button" | "submit";
  disabled?: boolean;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  children: ReactNode;
}

export function LoginButton({
  className,
  type = "button",
  disabled,
  onClick,
  children,
}: LoginButtonProps): JSX.Element {
  return (
    <button
      type={type === "submit" ? "submit" : "button"}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "w-[301.5px] max-w-full h-10 rounded p-2 flex items-center justify-center cursor-pointer transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
    >
      {children}
    </button>
  );
}
