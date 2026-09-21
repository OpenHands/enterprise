import React from "react";
import { cn } from "#/utils/utils";
import {
  formControlBorderClassName,
  formControlFocusWithinClassName,
  formControlRadiusClassName,
  formControlSurfaceClassName,
  formControlTransitionClassName,
} from "#/utils/form-control-classes";
import { BrandBadge } from "../badge";
import XIcon from "#/icons/x.svg?react";

interface BadgeInputProps {
  name?: string;
  value: string[];
  placeholder?: string;
  onChange: (value: string[]) => void;
  className?: string;
  inputClassName?: string;
}

export function BadgeInput({
  name,
  value,
  placeholder,
  onChange,
  className,
  inputClassName,
}: BadgeInputProps) {
  const [inputValue, setInputValue] = React.useState("");

  const commitInput = (text: string) => {
    // Pasted lists may hold several values split by whitespace/commas/semicolons
    const newBadges = text.split(/[\s,;]+/).filter(Boolean);
    if (newBadges.length > 0) onChange([...value, ...newBadges]);
    setInputValue("");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // If pressing Backspace with empty input, remove the last badge
    if (e.key === "Backspace" && inputValue === "" && value.length > 0) {
      const newBadges = [...value];
      newBadges.pop();
      onChange(newBadges);
      return;
    }

    // If pressing Space, Enter or comma with non-empty input, add a new badge
    if (
      (e.key === " " || e.key === "Enter" || e.key === ",") &&
      inputValue.trim() !== ""
    ) {
      e.preventDefault();
      commitInput(inputValue);
    }
  };

  const removeBadge = (indexToRemove: number) => {
    onChange(value.filter((_, index) => index !== indexToRemove));
  };

  return (
    <div
      className={cn(
        formControlRadiusClassName,
        formControlBorderClassName,
        formControlSurfaceClassName,
        formControlTransitionClassName,
        formControlFocusWithinClassName,
        "flex min-h-9 w-full min-w-0 flex-wrap items-center gap-2 px-3 py-2 text-sm text-white",
        className,
      )}
    >
      {value.map((badge, index) => (
        <div key={index}>
          <BrandBadge className="flex items-center gap-0.5 px-2.5 py-1 text-sm font-medium leading-4 tracking-normal text-[var(--oh-color-base)]">
            {badge}
            <button
              data-testid="remove-button"
              type="button"
              onClick={() => removeBadge(index)}
              className="cursor-pointer"
            >
              <XIcon width={14} height={14} color="#000000" />
            </button>
          </BrandBadge>
        </div>
      ))}
      <input
        data-testid={name || "badge-input"}
        name={name}
        value={inputValue}
        placeholder={value.length === 0 ? placeholder : ""}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => commitInput(inputValue)}
        className={cn(
          "min-w-[8rem] flex-grow bg-transparent text-inherit outline-none",
          "placeholder:text-tertiary-alt",
          inputClassName,
        )}
      />
    </div>
  );
}
