import React, { useMemo, useState } from "react";
import { useTranslation, Trans } from "react-i18next";
import { useNavigate } from "react-router";
import { I18nKey } from "#/i18n/declaration";
import { BrandButton } from "#/components/features/settings/brand-button";
import { LoadingSpinner } from "#/components/shared/loading-spinner";
import { EyeIcon, EyeOffIcon } from "#/components/shared/icons/inline-icons";
import { ApiKey, CreateApiKeyResponse } from "#/api/api-keys";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import { mutateWithToast } from "#/utils/mutate-with-toast";
import { CreateApiKeyModal } from "./create-api-key-modal";
import { DeleteApiKeyModal } from "./delete-api-key-modal";
import { NewApiKeyModal } from "./new-api-key-modal";
import { useApiKeys } from "#/hooks/query/use-api-keys";
import { useLlmApiKey } from "#/hooks/query/use-llm-api-key";
import { useRefreshLlmApiKey } from "#/hooks/mutation/use-refresh-llm-api-key";
import { useOrganizations } from "#/hooks/query/use-organizations";
import {
  settingsListIconActionButtonClassName,
  settingsListScrollContainerClassName,
  settingsListTableCellClassName,
  settingsListTableHeadClassName,
  settingsListTableHeaderCellClassName,
  settingsListTableRowClassName,
} from "#/utils/settings-list-classes";
import {
  formControlInlineInputClassName,
  formControlShellClassName,
} from "#/utils/form-control-classes";
import { cn } from "#/utils/utils";
import CopyIcon from "#/icons/copy.svg?react";
import RefreshIcon from "#/icons/u-refresh.svg?react";
import DeleteIcon from "#/icons/u-delete.svg?react";

interface LlmApiKeyManagerProps {
  llmApiKey: { key: string | null } | undefined;
  isLoadingLlmKey: boolean;
  isPaymentRequired: boolean;
  refreshLlmApiKey: ReturnType<typeof useRefreshLlmApiKey>;
}

function LlmApiKeyPaywall() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div className="flex flex-col gap-6">
      <h3 className="text-lg font-medium text-white">
        {t(I18nKey.SETTINGS$LLM_API_KEY)}
      </h3>
      <div className="bg-base-secondary border border-[var(--oh-border)] rounded-lg p-4 flex flex-col gap-4">
        <p className="text-sm leading-5 text-muted">
          {t(I18nKey.SETTINGS$LLM_API_KEY_PAYWALL_MESSAGE)}
        </p>
        <div>
          <BrandButton
            type="button"
            variant="primary"
            onClick={() => navigate("/settings/billing")}
          >
            {t(I18nKey.SETTINGS$LLM_API_KEY_BUY_NOW)}
          </BrandButton>
        </div>
      </div>
    </div>
  );
}

function LlmApiKeyManager({
  llmApiKey,
  isLoadingLlmKey,
  isPaymentRequired,
  refreshLlmApiKey,
}: LlmApiKeyManagerProps) {
  const { t } = useTranslation();
  const [showLlmApiKey, setShowLlmApiKey] = useState(false);

  const handleRefreshLlmApiKey = async () => {
    await mutateWithToast(refreshLlmApiKey, undefined, {
      success: t(I18nKey.SETTINGS$API_KEY_REFRESHED),
      error: t(I18nKey.ERROR$GENERIC),
    });
  };

  // Show paywall if payment is required
  if (isPaymentRequired) {
    return <LlmApiKeyPaywall />;
  }

  if (isLoadingLlmKey || !llmApiKey) {
    return null;
  }

  let keyDisplay = t(I18nKey.API$NO_KEY_AVAILABLE);
  if (llmApiKey.key) {
    keyDisplay = showLlmApiKey ? llmApiKey.key : "•".repeat(20);
  }

  return (
    <div className="flex flex-col gap-4">
      <h3 className="text-lg font-medium text-white">
        {t(I18nKey.SETTINGS$LLM_API_KEY)}
      </h3>
      <p className="text-sm leading-5 text-muted">
        {t(I18nKey.SETTINGS$LLM_API_KEY_DESCRIPTION)}
      </p>
      <div className={cn(formControlShellClassName, "pr-1.5")}>
        <div
          className={cn(
            formControlInlineInputClassName,
            "font-mono text-white truncate",
          )}
          title={showLlmApiKey && llmApiKey.key ? llmApiKey.key : undefined}
        >
          {keyDisplay}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {llmApiKey.key && (
            <button
              type="button"
              className={settingsListIconActionButtonClassName}
              aria-label={
                showLlmApiKey
                  ? t(I18nKey.EXPANDABLE_MESSAGE$HIDE_DETAILS)
                  : t(I18nKey.EXPANDABLE_MESSAGE$SHOW_DETAILS)
              }
              title={
                showLlmApiKey
                  ? t(I18nKey.EXPANDABLE_MESSAGE$HIDE_DETAILS)
                  : t(I18nKey.EXPANDABLE_MESSAGE$SHOW_DETAILS)
              }
              onClick={() => setShowLlmApiKey(!showLlmApiKey)}
            >
              {showLlmApiKey ? <EyeOffIcon /> : <EyeIcon />}
            </button>
          )}
          <button
            type="button"
            className={settingsListIconActionButtonClassName}
            aria-label={t(I18nKey.SETTINGS$REFRESH_LLM_API_KEY)}
            title={t(I18nKey.SETTINGS$REFRESH_LLM_API_KEY)}
            disabled={refreshLlmApiKey.isPending}
            onClick={handleRefreshLlmApiKey}
          >
            {refreshLlmApiKey.isPending ? (
              <LoadingSpinner size="small" />
            ) : (
              <RefreshIcon width={16} height={16} />
            )}
          </button>
          <button
            type="button"
            className={settingsListIconActionButtonClassName}
            aria-label={t(I18nKey.BUTTON$COPY)}
            title={t(I18nKey.BUTTON$COPY)}
            onClick={() => {
              if (llmApiKey.key) {
                navigator.clipboard.writeText(llmApiKey.key);
                displaySuccessToast(t(I18nKey.SETTINGS$API_KEY_COPIED));
              }
            }}
          >
            <CopyIcon width={16} height={16} />
          </button>
        </div>
      </div>
    </div>
  );
}

type ApiKeyStatus = "active" | "pending" | "expired";

const getApiKeyStatus = (key: ApiKey): ApiKeyStatus => {
  const now = Date.now();
  if (key.expires_at && new Date(key.expires_at).getTime() < now) {
    return "expired";
  }
  if (key.not_before && new Date(key.not_before).getTime() > now) {
    return "pending";
  }
  return "active";
};

const STATUS_BADGE_CLASSES: Record<ApiKeyStatus, string> = {
  active: "bg-green-500/15 text-green-400",
  pending: "bg-yellow-500/15 text-yellow-400",
  expired: "bg-red-500/15 text-red-400",
};

function formatApiKeyDate(dateString: string | null) {
  if (!dateString) return "—";
  return new Date(dateString).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function ApiKeyStatusBadge({
  status,
  notBefore,
  expiresAt,
}: {
  status: ApiKeyStatus;
  notBefore: string | null;
  expiresAt: string | null;
}) {
  const { t } = useTranslation();
  const labelKey = {
    active: I18nKey.SETTINGS$API_KEY_STATUS_ACTIVE,
    pending: I18nKey.SETTINGS$API_KEY_STATUS_PENDING,
    expired: I18nKey.SETTINGS$API_KEY_STATUS_EXPIRED,
  }[status];

  const windowParts: string[] = [];
  if (notBefore) {
    windowParts.push(
      `${t(I18nKey.SETTINGS$API_KEY_NOT_BEFORE)}: ${formatApiKeyDate(notBefore)}`,
    );
  }
  if (expiresAt) {
    windowParts.push(
      `${t(I18nKey.SETTINGS$API_KEY_EXPIRES_AT)}: ${formatApiKeyDate(expiresAt)}`,
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        STATUS_BADGE_CLASSES[status],
      )}
      title={windowParts.length > 0 ? windowParts.join(" · ") : undefined}
    >
      {t(labelKey)}
      {/* Keep window labels in the accessibility tree for tests/screen readers */}
      {windowParts.length > 0 && (
        <span className="sr-only"> {windowParts.join(" ")}</span>
      )}
    </span>
  );
}

interface ApiKeysTableProps {
  apiKeys: ApiKey[];
  isLoading: boolean;
  onDeleteKey: (key: ApiKey) => void;
}

function ApiKeyScopeBadge({
  orgId,
  orgLabel,
}: {
  orgId: string | null;
  orgLabel: string;
}) {
  const { t } = useTranslation();
  if (orgId === null) {
    // Unbound key -- usable against any org via X-Org-Id.
    return (
      <span
        className="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap bg-blue-500/15 text-blue-400"
        title={t(I18nKey.SETTINGS$API_KEY_ORG_ALL_ORGS_DESCRIPTION)}
      >
        {t(I18nKey.SETTINGS$API_KEY_SCOPE_ALL_ORGS)}
      </span>
    );
  }
  // Bound key -- show the org's display name (or the fallback label
  // when the org is no longer in the user's membership list).
  return (
    <span
      className="inline-flex max-w-full items-center truncate rounded-md px-2 py-0.5 text-xs font-medium bg-[var(--oh-surface-raised)] text-[var(--oh-muted)]"
      title={t(I18nKey.SETTINGS$API_KEY_SCOPE_BOUND_TITLE)}
    >
      {orgLabel}
    </span>
  );
}

function ApiKeysTable({ apiKeys, isLoading, onDeleteKey }: ApiKeysTableProps) {
  const { t } = useTranslation();
  const { data: organizationsData } = useOrganizations();

  // Map org id -> display label. Personal workspaces are rendered using
  // the same "Personal Workspace" string as the create modal so the
  // list, modal and header all read the same way.
  const orgLabelsById = useMemo(() => {
    const map = new Map<string, string>();
    for (const org of organizationsData?.organizations ?? []) {
      map.set(
        org.id,
        org.is_personal ? t(I18nKey.ORG$PERSONAL_WORKSPACE) : org.name,
      );
    }
    return map;
  }, [organizationsData, t]);

  const resolveOrgLabel = (orgId: string | null): string => {
    if (orgId === null) {
      // Unbound keys are rendered as their own badge; this shouldn't
      // be reached but keep a sensible fallback.
      return t(I18nKey.SETTINGS$API_KEY_SCOPE_ALL_ORGS);
    }
    return (
      orgLabelsById.get(orgId) ??
      // Fallback when the user can no longer see the org (e.g. they
      // left it). The id is short and unambiguous; the title carries
      // the full sentence.
      orgId.slice(0, 8)
    );
  };

  if (isLoading) {
    return (
      <div className="flex justify-center p-4">
        <LoadingSpinner size="large" />
      </div>
    );
  }

  if (!Array.isArray(apiKeys) || apiKeys.length === 0) {
    return null;
  }

  return (
    <div className={settingsListScrollContainerClassName}>
      <table className="w-full min-w-full table-fixed">
        <thead className={settingsListTableHeadClassName}>
          <tr>
            <th className={cn(settingsListTableHeaderCellClassName, "w-[22%]")}>
              {t(I18nKey.SETTINGS$NAME)}
            </th>
            <th className={cn(settingsListTableHeaderCellClassName, "w-[16%]")}>
              {t(I18nKey.SETTINGS$CREATED_AT)}
            </th>
            <th className={cn(settingsListTableHeaderCellClassName, "w-[16%]")}>
              {t(I18nKey.SETTINGS$LAST_USED)}
            </th>
            <th className={cn(settingsListTableHeaderCellClassName, "w-[14%]")}>
              {t(I18nKey.SETTINGS$API_KEY_STATUS)}
            </th>
            <th className={cn(settingsListTableHeaderCellClassName, "w-[22%]")}>
              {t(I18nKey.SETTINGS$API_KEY_SCOPE)}
            </th>
            <th
              className={cn(
                settingsListTableHeaderCellClassName,
                "w-[10%] text-right",
              )}
            >
              {t(I18nKey.SETTINGS$ACTIONS)}
            </th>
          </tr>
        </thead>
        <tbody>
          {apiKeys.map((key) => {
            const status = getApiKeyStatus(key);
            const dimmed =
              status === "expired" || status === "pending" ? "opacity-60" : "";
            return (
              <tr
                key={key.id}
                className={cn(settingsListTableRowClassName, dimmed)}
              >
                <td
                  className={cn(
                    settingsListTableCellClassName,
                    "truncate text-content-2",
                  )}
                  title={key.name}
                >
                  {key.name}
                </td>
                <td
                  className={cn(
                    settingsListTableCellClassName,
                    "whitespace-nowrap text-content-2",
                  )}
                  title={key.created_at ?? undefined}
                >
                  {formatApiKeyDate(key.created_at)}
                </td>
                <td
                  className={cn(
                    settingsListTableCellClassName,
                    "whitespace-nowrap text-content-2",
                  )}
                  title={key.last_used_at ?? undefined}
                >
                  {formatApiKeyDate(key.last_used_at)}
                </td>
                <td className={settingsListTableCellClassName}>
                  <ApiKeyStatusBadge
                    status={status}
                    notBefore={key.not_before}
                    expiresAt={key.expires_at}
                  />
                </td>
                <td className={cn(settingsListTableCellClassName, "min-w-0")}>
                  <ApiKeyScopeBadge
                    orgId={key.org_id}
                    orgLabel={resolveOrgLabel(key.org_id)}
                  />
                </td>
                <td
                  className={cn(settingsListTableCellClassName, "text-right")}
                >
                  <button
                    type="button"
                    onClick={() => onDeleteKey(key)}
                    aria-label={`Delete ${key.name}`}
                    className={settingsListIconActionButtonClassName}
                  >
                    <DeleteIcon width={16} height={16} />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function ApiKeysManager() {
  const { t } = useTranslation();
  const { data: apiKeys = [], isLoading, error } = useApiKeys();
  const {
    data: llmApiKey,
    isLoading: isLoadingLlmKey,
    isPaymentRequired,
  } = useLlmApiKey();
  const refreshLlmApiKey = useRefreshLlmApiKey();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  const [keyToDelete, setKeyToDelete] = useState<ApiKey | null>(null);
  const [newlyCreatedKey, setNewlyCreatedKey] =
    useState<CreateApiKeyResponse | null>(null);
  const [showNewKeyModal, setShowNewKeyModal] = useState(false);

  // Display error toast if the query fails (but not for payment required)
  if (error && !isPaymentRequired) {
    displayErrorToast(t(I18nKey.ERROR$GENERIC));
  }

  const handleKeyCreated = (newKey: CreateApiKeyResponse) => {
    setNewlyCreatedKey(newKey);
    setCreateModalOpen(false);
    setShowNewKeyModal(true);
  };

  const handleCloseCreateModal = () => {
    setCreateModalOpen(false);
  };

  const handleCloseDeleteModal = () => {
    setDeleteModalOpen(false);
    setKeyToDelete(null);
  };

  const handleCloseNewKeyModal = () => {
    setShowNewKeyModal(false);
    setNewlyCreatedKey(null);
  };

  const handleDeleteKey = (key: ApiKey) => {
    setKeyToDelete(key);
    setDeleteModalOpen(true);
  };

  return (
    <>
      <div className="flex flex-col gap-6">
        <LlmApiKeyManager
          llmApiKey={llmApiKey}
          isLoadingLlmKey={isLoadingLlmKey}
          isPaymentRequired={isPaymentRequired}
          refreshLlmApiKey={refreshLlmApiKey}
        />

        <div className="mt-2 flex flex-col gap-4 border-t border-[var(--oh-border)] pt-6">
          <div className="flex items-center justify-between gap-4">
            <h3 className="text-lg font-medium text-white">
              {t(I18nKey.SETTINGS$OPENHANDS_API_KEYS)}
            </h3>
            <BrandButton
              type="button"
              variant="primary"
              onClick={() => setCreateModalOpen(true)}
            >
              {t(I18nKey.SETTINGS$CREATE_API_KEY)}
            </BrandButton>
          </div>

          <p className="text-sm leading-5 text-muted">
            <Trans
              i18nKey={I18nKey.SETTINGS$API_KEYS_DESCRIPTION}
              components={{
                a: (
                  <a
                    href="https://docs.all-hands.dev/usage/cloud/cloud-api"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:underline"
                  >
                    API documentation
                  </a>
                ),
              }}
            />
          </p>

          <ApiKeysTable
            apiKeys={apiKeys}
            isLoading={isLoading}
            onDeleteKey={handleDeleteKey}
          />
        </div>
      </div>

      {/* Create API Key Modal */}
      <CreateApiKeyModal
        isOpen={createModalOpen}
        onClose={handleCloseCreateModal}
        onKeyCreated={handleKeyCreated}
      />

      {/* Delete API Key Modal */}
      <DeleteApiKeyModal
        isOpen={deleteModalOpen}
        keyToDelete={keyToDelete}
        onClose={handleCloseDeleteModal}
      />

      {/* Show New API Key Modal */}
      <NewApiKeyModal
        isOpen={showNewKeyModal}
        newlyCreatedKey={newlyCreatedKey}
        onClose={handleCloseNewKeyModal}
      />
    </>
  );
}
