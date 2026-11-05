import { useMutation } from "@tanstack/react-query";
import { SecretsService } from "#/api/secrets-service";
import { organizationService } from "#/api/organization-service/organization-service.api";

export const useUpdateSecret = () =>
  useMutation({
    mutationFn: ({
      secretToEdit,
      name,
      description,
      isShared,
      organizationId,
    }: {
      secretToEdit: string;
      name: string;
      description?: string;
      /** When true (and organizationId is set), update the org-shared
       * secret via the org-secrets endpoint. */
      isShared?: boolean;
      organizationId?: string | null;
    }) => {
      if (isShared && organizationId) {
        return organizationService.updateOrgSecret(
          organizationId,
          secretToEdit,
          { name, description },
        );
      }
      return SecretsService.updateSecret(secretToEdit, name, description);
    },
  });
