import { openHands } from "../open-hands-axios";
import type {
  CreateWarmRuntimeParams,
  WarmRuntime,
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

  deleteWarmRuntime: async ({ name }: { name: string }) => {
    await openHands.delete(
      `/api/admin/warm-runtimes/${encodeURIComponent(name)}`,
    );
  },
};
