import { useTranslation } from "react-i18next";
import { I18nKey } from "#/i18n/declaration";
import { ModalBackdrop } from "#/components/shared/modals/modal-backdrop";
import { MCPServerForm } from "./mcp-server-form";

type MCPServerType = "sse" | "stdio" | "shttp";

interface MCPServerConfig {
  id: string;
  type: MCPServerType;
  name?: string;
  url?: string;
  api_key?: string;
  timeout?: number;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
}

interface MCPServerModalProps {
  mode: "add" | "edit";
  server?: MCPServerConfig;
  existingServers: MCPServerConfig[];
  onSubmit: (server: MCPServerConfig) => void;
  onClose: () => void;
}

export function MCPServerModal({
  mode,
  server,
  existingServers,
  onSubmit,
  onClose,
}: MCPServerModalProps) {
  const { t } = useTranslation();

  return (
    <ModalBackdrop
      onClose={onClose}
      aria-label={
        mode === "add"
          ? t(I18nKey.SETTINGS$MCP_ADD_SERVER)
          : t(I18nKey.SETTINGS$MCP_EDIT_CONFIGURATION)
      }
    >
      <div
        data-testid={
          mode === "add" ? "add-mcp-server-modal" : "edit-mcp-server-modal"
        }
        className="flex max-h-[90vh] w-[560px] max-w-[90vw] flex-col gap-4 overflow-y-auto rounded-xl border border-[var(--oh-border)] bg-base-secondary p-6 custom-scrollbar"
      >
        <h3 className="text-xl font-bold">
          {mode === "add"
            ? t(I18nKey.SETTINGS$MCP_ADD_SERVER)
            : t(I18nKey.SETTINGS$MCP_EDIT_CONFIGURATION)}
        </h3>
        <MCPServerForm
          mode={mode}
          server={server}
          existingServers={existingServers}
          onSubmit={onSubmit}
          onCancel={onClose}
        />
      </div>
    </ModalBackdrop>
  );
}
