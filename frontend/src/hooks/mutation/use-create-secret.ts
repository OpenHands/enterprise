import { useMutation } from "@tanstack/react-query";
import { SecretsService } from "#/api/secrets-service";
import { organizationService } from "#/api/organization-service/organization-service.api";

export const useCreateSecret = () =>
  useMutation({
    mutationFn: ({
      name,
      value,
      description,
      isShared,
      organizationId,
    }: {
      name: string;
      value: string;
      description?: string;
      /** When true (and organizationId is set), create the secret as an
       * org-shared secret via the org-secrets endpoint instead of the
       * personal V1 secrets endpoint. */
      isShared?: boolean;
      organizationId?: string | null;
    }) => {
      if (isShared && organizationId) {
        return organizationService.createOrgSecret(organizationId, {
          name,
          value,
          description,
        });
      }
      return SecretsService.createSecret(name, value, description);
    },
  });
