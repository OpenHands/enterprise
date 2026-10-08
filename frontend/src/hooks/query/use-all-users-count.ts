import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { adminService } from "#/api/admin-service/admin-service.api";

interface UseAllUsersCountParams {
  email?: string;
  enabled?: boolean;
}

export const useAllUsersCount = ({
  email,
  enabled = true,
}: UseAllUsersCountParams = {}) =>
  useQuery({
    queryKey: ["admin", "users", "count", email],
    queryFn: () => adminService.getAllUsersCount({ email }),
    enabled,
    placeholderData: keepPreviousData,
  });
