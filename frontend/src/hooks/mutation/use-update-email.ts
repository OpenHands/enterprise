import type { UseMutationResult, DefaultError } from "@tanstack/react-query";
import { useMutation } from "@tanstack/react-query";
import UserService from "#/api/user-service/user-service.api";

export const useUpdateEmail = (): UseMutationResult<
  void,
  DefaultError,
  string,
  unknown
> => useMutation({ mutationFn: UserService.updateEmail });
