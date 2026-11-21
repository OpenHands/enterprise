import { useMutation } from "@tanstack/react-query";
import ProfilesService, {
  ValidateLlmProfileRequest,
} from "#/api/settings-service/profiles-service.api";

interface ValidateLlmProfileVariables {
  name: string;
  request: ValidateLlmProfileRequest;
}

export function useValidateLlmProfile() {
  return useMutation({
    mutationFn: ({ name, request }: ValidateLlmProfileVariables) =>
      ProfilesService.validateProfile(name, request),
  });
}
