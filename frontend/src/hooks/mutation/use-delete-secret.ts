import { useMutation } from "@tanstack/react-query";
import { SecretsService } from "#/api/secrets-service";
import { organizationService } from "#/api/organization-service/organization-service.api";

export const useDeleteSecret = () =>
  useMutation({
    mutationFn: ({
      id,
      isShared,
      organizationId,
    }: {
      id: string;
      /** When true (and organizationId is set), delete the org-shared
       * secret via the org-secrets endpoint. */
      isShared?: boolean;
      organizationId?: string | null;
    }) => {
      if (isShared && organizationId) {
        return organizationService.deleteOrgSecret(organizationId, id);
      }
      return SecretsService.deleteSecret(id);
    },
  });
