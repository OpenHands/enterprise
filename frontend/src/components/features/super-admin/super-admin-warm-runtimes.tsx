import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { Plus } from "lucide-react";
import { OrgModal } from "#/components/shared/modals/org-modal";
import { useDeleteWarmRuntime } from "#/hooks/mutation/use-warm-runtime-mutations";
import { useWarmRuntimes } from "#/hooks/query/use-warm-runtimes";
import type { WarmRuntime } from "#/api/warm-runtimes-service/warm-runtimes.types";
import { I18nKey } from "#/i18n/declaration";
import {
  SUPER_ADMIN_NAV_ITEMS,
  SUPER_ADMIN_PATHS,
} from "#/constants/super-admin-nav";
import {
  BrandButton,
  SuperAdminPageHeader,
  SuperAdminRowMenu,
  SuperAdminTable,
} from "./super-admin-chrome";
import { summarizeResources } from "./warm-runtime-form";

const navCopy = () =>
  SUPER_ADMIN_NAV_ITEMS.find(
    (item) => item.to === SUPER_ADMIN_PATHS.warmRuntimes,
  ) ?? SUPER_ADMIN_NAV_ITEMS[0];

export const warmRuntimePath = (name: string) =>
  `${SUPER_ADMIN_PATHS.warmRuntimes}/edit/${encodeURIComponent(name)}`;

export function SuperAdminWarmRuntimes() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const copy = navCopy();
  const { data, isLoading, isError } = useWarmRuntimes();
  const deleteRuntime = useDeleteWarmRuntime();
  const [runtimeToDelete, setRuntimeToDelete] = useState<WarmRuntime | null>(
    null,
  );

  const runtimes = useMemo(() => data ?? [], [data]);
  const hasConfigMapRuntimes = runtimes.some((row) => row.source === "file");

  const confirmDelete = () => {
    if (!runtimeToDelete) {
      return;
    }
    deleteRuntime.mutate(
      { name: runtimeToDelete.name },
      { onSuccess: () => setRuntimeToDelete(null) },
    );
  };

  return (
    <div
      className="flex flex-col gap-4"
      data-testid="super-admin-warm-runtimes"
    >
      <SuperAdminPageHeader
        title={t(copy.text)}
        subtitle={t(copy.subtitle)}
        action={
          <BrandButton
            type="button"
            variant="primary"
            testId="super-admin-add-warm-runtime"
            startContent={<Plus className="h-4 w-4" />}
            onClick={() => navigate(`${SUPER_ADMIN_PATHS.warmRuntimes}/new`)}
          >
            {t(I18nKey.SUPER_ADMIN$ADD_WARM_RUNTIME)}
          </BrandButton>
        }
      />
      {hasConfigMapRuntimes ? (
        <p
          data-testid="super-admin-warm-runtimes-configmap-notice"
          className="text-xs text-tertiary-alt"
        >
          {t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_CONFIGMAP_NOTICE)}
        </p>
      ) : null}
      {isLoading ? (
        <p className="text-sm text-[var(--oh-muted)]">
          {t(I18nKey.SUPER_ADMIN$LOADING)}
        </p>
      ) : null}
      {isError ? (
        <p className="text-sm text-red-400">
          {t(I18nKey.SUPER_ADMIN$LOAD_ERROR)}
        </p>
      ) : null}
      <SuperAdminTable<WarmRuntime>
        testId="super-admin-warm-runtimes-table"
        rows={runtimes}
        getRowKey={(row) => row.name}
        empty={t(I18nKey.SUPER_ADMIN$EMPTY_WARM_RUNTIMES)}
        columns={[
          {
            key: "name",
            header: t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_NAME),
            className: "w-[18%]",
            render: (row) => <span className="block truncate">{row.name}</span>,
          },
          {
            key: "image",
            header: t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_IMAGE),
            className: "w-[34%]",
            render: (row) => (
              <span className="block truncate" title={row.image}>
                {row.image}
              </span>
            ),
          },
          {
            key: "resources",
            header: t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COL_RESOURCES),
            className: "w-[14%]",
            render: (row) => summarizeResources(row) || "—",
          },
          {
            key: "idle",
            header: t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COL_IDLE_TIMEOUT),
            className: "w-[12%]",
            render: (row) => (row.idle_seconds ? `${row.idle_seconds}s` : "—"),
          },
          {
            key: "source",
            header: t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_COL_SOURCE),
            className: "w-[12%]",
            render: (row) =>
              row.source === "file"
                ? t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_SOURCE_FILE)
                : t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_SOURCE_DB),
          },
          {
            key: "actions",
            header: "",
            className: "w-12 min-w-12 text-right",
            render: (row) => (
              <SuperAdminRowMenu
                testId={`super-admin-warm-runtime-actions-${row.name}`}
                ariaLabel={t(I18nKey.SUPER_ADMIN$ROW_ACTIONS)}
                items={[
                  {
                    label: t(I18nKey.BUTTON$EDIT),
                    testId: `super-admin-warm-runtime-edit-${row.name}`,
                    onSelect: () => navigate(warmRuntimePath(row.name)),
                  },
                  {
                    label: t(I18nKey.BUTTON$DELETE),
                    testId: `super-admin-warm-runtime-delete-${row.name}`,
                    destructive: true,
                    isDisabled: row.source === "file",
                    title:
                      row.source === "file"
                        ? t(I18nKey.SUPER_ADMIN$WARM_RUNTIME_DELETE_CONFIGMAP)
                        : undefined,
                    onSelect: () => setRuntimeToDelete(row),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      {runtimeToDelete ? (
        <OrgModal
          testId="super-admin-warm-runtime-delete-confirm"
          title={t(I18nKey.SUPER_ADMIN$DELETE_WARM_RUNTIME_TITLE)}
          description={
            <Trans
              i18nKey={I18nKey.SUPER_ADMIN$DELETE_WARM_RUNTIME_CONFIRM}
              values={{ name: runtimeToDelete.name }}
              components={{ name: <span className="text-white" /> }}
            />
          }
          primaryButtonText={t(I18nKey.BUTTON$CONFIRM)}
          secondaryButtonText={t(I18nKey.BUTTON$CANCEL)}
          primaryButtonTestId="super-admin-warm-runtime-delete-confirm-submit"
          secondaryButtonTestId="super-admin-warm-runtime-delete-confirm-cancel"
          onPrimaryClick={confirmDelete}
          onClose={() => setRuntimeToDelete(null)}
          isLoading={deleteRuntime.isPending}
        />
      ) : null}
    </div>
  );
}
