import { openHands } from "../open-hands-axios";
import type {
  BudgetAdoptionRequest,
  BudgetControlState,
  BudgetNotificationState,
  BudgetNotificationUpdate,
  BudgetOperation,
  BudgetPolicyUpdate,
  BudgetPreview,
} from "./budget-service.types";

export type ScopedBudgetRequest<T> = { orgId: string; request: T };
export type ScopedBudgetOperation = { orgId: string; operationId: string };

const base = (orgId: string) =>
  `/api/organizations/${encodeURIComponent(orgId)}/budgets`;

export const budgetService = {
  getNotifications: async (orgId: string) => {
    const { data } = await openHands.get<BudgetNotificationState>(
      `${base(orgId)}/notifications`,
    );
    return data;
  },
  updateNotifications: async ({
    orgId,
    request,
  }: ScopedBudgetRequest<BudgetNotificationUpdate>) => {
    const { data } = await openHands.put<BudgetNotificationState>(
      `${base(orgId)}/notifications`,
      request,
    );
    return data;
  },
  preview: async (orgId: string) => {
    const { data } = await openHands.get<BudgetPreview>(
      `${base(orgId)}/adoption/preview`,
    );
    return data;
  },
  confirm: async ({
    orgId,
    request,
  }: ScopedBudgetRequest<BudgetAdoptionRequest>) => {
    const { data } = await openHands.post<BudgetOperation>(
      `${base(orgId)}/adoption`,
      request,
    );
    return data;
  },
  update: async ({
    orgId,
    request,
  }: ScopedBudgetRequest<BudgetPolicyUpdate>) => {
    const { data } = await openHands.patch<BudgetOperation>(
      `${base(orgId)}/policy`,
      request,
    );
    return data;
  },
  getOperation: async ({ orgId, operationId }: ScopedBudgetOperation) => {
    const { data } = await openHands.get<BudgetOperation>(
      `${base(orgId)}/operations/${encodeURIComponent(operationId)}`,
    );
    return data;
  },
  retry: async ({ orgId, operationId }: ScopedBudgetOperation) => {
    const { data } = await openHands.post<BudgetOperation>(
      `${base(orgId)}/operations/${encodeURIComponent(operationId)}/retry`,
    );
    return data;
  },
  handOff: async ({
    orgId,
    request,
  }: ScopedBudgetRequest<{ expected_generation: number }>) => {
    const { data } = await openHands.post<BudgetControlState>(
      `${base(orgId)}/handoff`,
      request,
    );
    return data;
  },
};
