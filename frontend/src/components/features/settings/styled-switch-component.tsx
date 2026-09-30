import { ToggleSwitchVisual } from "#/ui/toggle-switch";

interface StyledSwitchComponentProps {
  isToggled: boolean;
}

export function StyledSwitchComponent({
  isToggled,
}: StyledSwitchComponentProps) {
  return <ToggleSwitchVisual enabled={isToggled} />;
}
