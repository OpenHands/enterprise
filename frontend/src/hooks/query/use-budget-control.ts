import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { budgetService } from "#/api/budget-service/budget-service.api";
import { organizationService } from "#/api/organization-service/organization-service.api";

export const budgetOperationKey = (orgId: string, operationId: string) =>
  ["budget-control", orgId, "operation", operationId] as const;

export const budgetNotificationKey = (orgId: string) =>
  ["budget-control", orgId, "notifications"] as const;

export const useBudgetNotifications = (orgId: string) =>
  useQuery({
    queryKey: budgetNotificationKey(orgId),
    queryFn: () => budgetService.getNotifications(orgId),
    enabled: !!orgId,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

export const useBudgetSettings = (
  orgId: string,
  page: number,
  search: string,
) =>
  useQuery({
    queryKey: ["organizations", "budgets", orgId, page, search],
    queryFn: () =>
      organizationService.getBudgetSettings({
        orgId,
        usersPage: page,
        usersPerPage: 50,
        usersSearch: search || undefined,
      }),
    enabled: !!orgId,
    placeholderData: (previous, query) =>
      query?.queryKey[2] === orgId ? previous : undefined,
  });

export const useBudgetAdoptionPreview = (orgId: string, enabled: boolean) =>
  useQuery({
    queryKey: ["budget-control", orgId, "preview"],
    queryFn: () => budgetService.preview(orgId),
    enabled: !!orgId && enabled,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

export const useBudgetOperation = (
  orgId: string,
  operationId: string | null,
) => {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: budgetOperationKey(orgId, operationId ?? ""),
    queryFn: () =>
      budgetService.getOperation({ orgId, operationId: operationId! }),
    enabled: !!orgId && !!operationId,
    retry: false,
    refetchInterval: (operationQuery) =>
      operationQuery.state.data?.status === "applied" ||
      operationQuery.state.data?.status === "abandoned"
        ? false
        : 1500,
  });

  const status = query.data?.status;
  useEffect(() => {
    if (status === "applied" || status === "abandoned") {
      queryClient.invalidateQueries({
        queryKey: ["organizations", "budgets", orgId],
      });
    }
  }, [queryClient, orgId, operationId, status]);

  return query;
};
