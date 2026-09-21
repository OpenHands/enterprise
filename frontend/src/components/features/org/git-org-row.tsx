import { cn, getProviderName } from "#/utils/utils";
import { Text } from "#/ui/typography";
import type { GitOrg } from "#/types/org";
import type { Provider } from "#/types/settings";
import { settingsListRowClassName } from "#/utils/settings-list-classes";
import { ClaimButton } from "./claim-button";

interface GitOrgRowProps {
  org: GitOrg;
  onClaim: (id: string) => void;
  onDisconnect: (id: string) => void;
}

export function GitOrgRow({ org, onClaim, onDisconnect }: GitOrgRowProps) {
  return (
    <div
      data-testid={`org-row-${org.id}`}
      className={cn(settingsListRowClassName, "justify-between")}
    >
      <span className="min-w-0 truncate text-sm font-normal leading-5">
        <Text className="text-[var(--oh-muted)]">
          {getProviderName(org.provider.toLowerCase() as Provider)}/
        </Text>
        <Text className="text-white">{org.name}</Text>
      </span>
      <ClaimButton org={org} onClaim={onClaim} onDisconnect={onDisconnect} />
    </div>
  );
}
