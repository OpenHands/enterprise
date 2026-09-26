import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { BrandButton } from "#/components/features/settings/brand-button";
import { Typography } from "#/ui/typography";
import { ProviderConnectionRow } from "./provider-connection-row";
import { ProviderConnectionModal } from "./provider-connection-modal";
import { DeleteProviderConnectionModal } from "./delete-provider-connection-modal";
import { useProviderConnections } from "#/hooks/query/use-provider-connections";
import type { ProviderConnection } from "#/api/organization-service/org-provider-connections-service.api";
import { I18nKey } from "#/i18n/declaration";
import {
  settingsListContainerClassName,
  settingsListDividerClassName,
} from "#/utils/settings-list-classes";
import { cn } from "#/utils/utils";

interface ProviderConnectionsManagerProps {
  orgId: string | null | undefined;
  /** Whether the current user may manage provider connections. */
  canManage?: boolean;
  /** Profile summaries, used to count links per connection. */
  profiles?: ReadonlyArray<{
    provider_connection_id?: string | null;
  }>;
}

/**
 * The provider connections list. The server returns `{ connections: [...] }`
 * with no key material — only an `api_key_set` flag — so this surface is safe
 * to render for any org member. Only users with edit permission get the
 * add/edit/delete controls.
 */
export function ProviderConnectionsManager({
  orgId,
  canManage = false,
  profiles = [],
}: ProviderConnectionsManagerProps) {
  const { t } = useTranslation();
  const { data: connections, isLoading, error } = useProviderConnections(orgId);

  const linkedCountById = useMemo(() => {
    const counts = new Map<string, number>();
    for (const profile of profiles) {
      const id = profile.provider_connection_id;
      if (id) counts.set(id, (counts.get(id) ?? 0) + 1);
    }
    return counts;
  }, [profiles]);

  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ProviderConnection | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProviderConnection | null>(
    null,
  );

  const renderBody = () => {
    if (isLoading) {
      return (
        <div className="flex justify-center p-4">
          <LoadingSpinner size="large" />
        </div>
      );
    }
    if (error) {
      return (
        <Typography.Paragraph className="text-sm text-red-400">
          {t(I18nKey.SETTINGS$PROVIDER_CONNECTIONS_LOAD_ERROR)}
        </Typography.Paragraph>
      );
    }
    if (!connections || connections.length === 0) {
      return (
        <Typography.Paragraph className="text-sm text-[var(--oh-muted)] italic">
          {t(I18nKey.SETTINGS$PROVIDER_CONNECTIONS_EMPTY)}
        </Typography.Paragraph>
      );
    }
    return (
      <div
        className={cn(
          settingsListContainerClassName,
          settingsListDividerClassName,
        )}
      >
        {connections.map((connection) => (
          <ProviderConnectionRow
            key={connection.id}
            connection={connection}
            linkedProfileCount={linkedCountById.get(connection.id) ?? 0}
            canManage={canManage}
            onEdit={setEditTarget}
            onDelete={setDeleteTarget}
          />
        ))}
      </div>
    );
  };

  return (
    <section
      data-testid="provider-connections-manager"
      className="flex flex-col gap-2"
    >
      <div className="flex items-center justify-between gap-2">
        <Typography.Text className="text-base font-medium">
          {t(I18nKey.SETTINGS$PROVIDER_CONNECTIONS_TITLE)}
        </Typography.Text>
        {canManage ? (
          <BrandButton
            testId="add-provider-connection"
            type="button"
            variant="secondary"
            className="h-fit"
            onClick={() => setCreateOpen(true)}
          >
            {t(I18nKey.SETTINGS$PROVIDER_CONNECTION_ADD)}
          </BrandButton>
        ) : null}
      </div>
      <Typography.Paragraph className="text-sm text-[var(--oh-muted)]">
        {t(I18nKey.SETTINGS$PROVIDER_CONNECTIONS_SUBLINE)}
      </Typography.Paragraph>

      {renderBody()}

      {canManage ? (
        <>
          {createOpen ? (
            <ProviderConnectionModal
              orgId={orgId}
              isCreate
              onClose={() => setCreateOpen(false)}
            />
          ) : null}
          {editTarget ? (
            <ProviderConnectionModal
              orgId={orgId}
              connection={editTarget}
              isCreate={false}
              onClose={() => setEditTarget(null)}
            />
          ) : null}
          {deleteTarget ? (
            <DeleteProviderConnectionModal
              orgId={orgId}
              connection={deleteTarget}
              onClose={() => setDeleteTarget(null)}
            />
          ) : null}
        </>
      ) : null}
    </section>
  );
}
