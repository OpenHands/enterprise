import { useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { SecretsService } from "#/api/secrets-service";
import { I18nKey } from "#/i18n/declaration";
import {
  displayErrorToast,
  displaySuccessToast,
} from "#/utils/custom-toast-handlers";
import type { McpLibraryField } from "#/utils/mcp-library";

/** Truncates a list of key names for display in toasts. */
function formatKeyList(keys: string[]): string {
  const MAX = 3;
  if (keys.length <= MAX) return keys.join(", ");
  return `${keys.slice(0, MAX).join(", ")} … (+${keys.length - MAX})`;
}

/**
 * Returns a function that saves the checked, non-empty fields of an MCP
 * Library install as personal secrets, named after each field's key. Saving
 * is an upsert, so a secret of the same name is overwritten. Failures only
 * show a toast: the server itself is already installed.
 */
export function useSaveFieldsAsSecrets() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  return useCallback(
    (
      fields: McpLibraryField[],
      values: Record<string, string>,
      savedAsSecret: Record<string, boolean>,
    ): Promise<void> => {
      const fieldsToSave = fields.filter(
        (field) => savedAsSecret[field.key] && (values[field.key] ?? "").trim(),
      );
      if (fieldsToSave.length === 0) return Promise.resolve();

      return Promise.allSettled(
        fieldsToSave.map((field) =>
          SecretsService.createSecret(
            field.key,
            values[field.key].trim(),
            field.label,
          ),
        ),
      ).then((results) => {
        const saved = fieldsToSave
          .filter((_, i) => results[i].status === "fulfilled")
          .map((field) => field.key);
        const failed = fieldsToSave
          .filter((_, i) => results[i].status === "rejected")
          .map((field) => field.key);

        if (saved.length > 0) {
          queryClient.invalidateQueries({ queryKey: ["secrets-search"] });
          queryClient.invalidateQueries({ queryKey: ["secrets"] });
          displaySuccessToast(
            t(I18nKey.MCP$SECRETS_SAVED, { keys: formatKeyList(saved) }),
          );
        }
        if (failed.length > 0) {
          displayErrorToast(
            t(I18nKey.MCP$SECRETS_SAVE_FAILED, { keys: formatKeyList(failed) }),
          );
        }
      });
    },
    [t, queryClient],
  );
}
