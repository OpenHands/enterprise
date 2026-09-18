import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import ProfileIcon from "#/icons/profile.svg?react";
import { cn } from "#/utils/utils";
import { Avatar } from "./avatar";

interface UserAvatarProps {
  avatarUrl?: string;
  isLoading?: boolean;
  /** When false, render a non-interactive span (e.g. nested inside another button). */
  interactive?: boolean;
}

export function UserAvatar({
  avatarUrl,
  isLoading,
  interactive = true,
}: UserAvatarProps) {
  const { t } = useTranslation();

  const content = (
    <>
      {!isLoading && avatarUrl && <Avatar src={avatarUrl} />}
      {!isLoading && !avatarUrl && (
        <ProfileIcon
          aria-label={t(I18nKey.USER$AVATAR_PLACEHOLDER)}
          width={28}
          height={28}
          className="text-muted"
        />
      )}
      {isLoading && <LoadingSpinner size="small" />}
    </>
  );

  const className = cn(
    "w-8 h-8 rounded-full flex items-center justify-center",
    interactive && "cursor-pointer",
    isLoading && "bg-transparent",
  );

  if (!interactive) {
    return (
      <span data-testid="user-avatar" className={className} aria-hidden>
        {content}
      </span>
    );
  }

  return (
    <button type="button" data-testid="user-avatar" className={className}>
      {content}
    </button>
  );
}
