import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { SecretForm } from "#/components/features/settings/secrets-settings/secret-form";
import { OrgMcpServerCreate } from "#/api/org-mcp-servers.types";
import { OrgMcpServerView } from "#/utils/org-mcp-servers";
import { OrgMcpServerForm } from "./org-mcp-server-form";
import type { TestMessage } from "./mcp-server-form";

const MODAL_CLASS_NAME =
  "flex max-h-[90vh] w-[560px] max-w-[90vw] flex-col gap-4 overflow-y-auto rounded-xl border border-[var(--oh-border)] bg-base-secondary p-6 custom-scrollbar";

interface OrgMcpServerModalProps {
  mode: "add" | "edit";
  listing?: OrgMcpServerView;
  existingNames: string[];
  onSubmit: (payload: OrgMcpServerCreate) => void;
  onClose: () => void;
  onTest?: (payload: OrgMcpServerCreate) => void;
  isTestPending?: boolean;
  isSaving?: boolean;
  testMessage?: TestMessage | null;
}

/** Add or edit an organization MCP server listing (admins / owners). */
export function OrgMcpServerModal({
  mode,
  listing,
  existingNames,
  onSubmit,
  onClose,
  onTest,
  isTestPending,
  isSaving,
  testMessage,
}: OrgMcpServerModalProps) {
  const { t } = useTranslation();
  const title =
    mode === "add"
      ? t(I18nKey.SETTINGS$MCP_ORG_ADD_SERVER)
      : t(I18nKey.SETTINGS$MCP_ORG_EDIT_SERVER);

  return (
    <ModalBackdrop onClose={onClose} aria-label={title}>
      <div
        data-testid={
          mode === "add"
            ? "add-org-mcp-server-modal"
            : "edit-org-mcp-server-modal"
        }
        className={MODAL_CLASS_NAME}
      >
        <h3 className="text-xl font-bold">{title}</h3>
        <OrgMcpServerForm
          mode={mode}
          listing={listing}
          existingNames={existingNames}
          onSubmit={onSubmit}
          onCancel={onClose}
          onTest={onTest}
          isTestPending={isTestPending}
          isSaving={isSaving}
          testMessage={testMessage}
        />
      </div>
    </ModalBackdrop>
  );
}

interface OrgMcpConnectModalProps {
  listing: OrgMcpServerView;
  onClose: () => void;
}

/**
 * Connect a user-managed listing: save the personal secret it references.
 * Only that member can use the secret, so the server runs under their own
 * identity.
 */
export function OrgMcpConnectModal({
  listing,
  onClose,
}: OrgMcpConnectModalProps) {
  const { t } = useTranslation();
  // A listing may reference several secrets; connect the first missing one
  // at a time. The caller only opens this while at least one is missing.
  const secretName = listing.secretNames[0] ?? "";
  const title = t(I18nKey.SETTINGS$MCP_ORG_CONNECT_TITLE, {
    name: listing.name,
  });

  return (
    <ModalBackdrop onClose={onClose} aria-label={title}>
      <div
        data-testid="connect-org-mcp-server-modal"
        className={MODAL_CLASS_NAME}
      >
        <h3 className="text-xl font-bold">{title}</h3>
        <p className="text-sm leading-5 text-muted">
          {t(I18nKey.SETTINGS$MCP_ORG_CONNECT_DESCRIPTION, {
            secret: secretName,
          })}
        </p>
        <SecretForm
          mode="add"
          selectedSecret={null}
          initialName={secretName}
          lockName
          onCancel={onClose}
        />
      </div>
    </ModalBackdrop>
  );
}
