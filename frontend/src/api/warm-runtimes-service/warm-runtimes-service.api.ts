import { openHands } from "../open-hands-axios";
import type {
  CreateWarmRuntimeParams,
  UpdateWarmRuntimeAccessParams,
  WarmRuntime,
  WarmRuntimeAccess,
  WarmRuntimeFields,
  WarmRuntimeList,
} from "./warm-runtimes.types";

export const warmRuntimesService = {
  listWarmRuntimes: async () => {
    const { data } = await openHands.get<WarmRuntimeList>(
      "/api/admin/warm-runtimes",
    );
    return data.configs;
  },

  createWarmRuntime: async (params: CreateWarmRuntimeParams) => {
    const { data } = await openHands.post<WarmRuntime>(
      "/api/admin/warm-runtimes",
      params,
    );
    return data;
  },

  updateWarmRuntime: async ({
    name,
    ...fields
  }: WarmRuntimeFields & { name: string }) => {
    const { data } = await openHands.put<WarmRuntime>(
      `/api/admin/warm-runtimes/${encodeURIComponent(name)}`,
      fields,
    );
    return data;
  },

  getWarmRuntimeAccess: async (name: string) => {
    const { data } = await openHands.get<WarmRuntimeAccess>(
      `/api/admin/warm-runtimes/${encodeURIComponent(name)}/access`,
    );
    return data;
  },

  updateWarmRuntimeAccess: async ({
    name,
    ...access
  }: UpdateWarmRuntimeAccessParams) => {
    const { data } = await openHands.put<WarmRuntimeAccess>(
      `/api/admin/warm-runtimes/${encodeURIComponent(name)}/access`,
      access,
    );
    return data;
  },

  deleteWarmRuntime: async ({ name }: { name: string }) => {
    await openHands.delete(
      `/api/admin/warm-runtimes/${encodeURIComponent(name)}`,
    );
  },
};
