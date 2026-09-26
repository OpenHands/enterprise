import React from "react";
import { useQuery } from "@tanstack/react-query";
import ConfigService from "#/api/config-service/config-service.api";
import type { LLMModel } from "#/api/config-service/config-service.types";
import type { FreeModelSet } from "#/utils/format-model-name";
import { useFreeModelsStore } from "#/stores/free-models-store";
import { useOrgTypeAndAccess } from "#/hooks/use-org-type-and-access";

/**
 * Provider whose models carry free / default metadata. Both are
 * OpenHands-managed concepts, so they are scoped to the `openhands` provider.
 */
const OPENHANDS_PROVIDER = "openhands";
const CLOUD_MODEL_SEARCH_PAGE_LIMIT = 100;
const MAX_PAGINATION_DEPTH = 10;

async function fetchAllOpenHandsModels(
  pageId: string | null | undefined,
  seenPageIds: Set<string>,
  depth: number,
): Promise<LLMModel[]> {
  if (depth >= MAX_PAGINATION_DEPTH) {
    throw new Error(
      `Too many pagination requests while fetching OpenHands models (depth=${depth})`,
    );
  }

  const page = await ConfigService.searchModels({
    provider__eq: OPENHANDS_PROVIDER,
    limit: CLOUD_MODEL_SEARCH_PAGE_LIMIT,
    ...(pageId ? { page_id: pageId } : {}),
  });

  if (!page.next_page_id) {
    return page.items;
  }

  if (seenPageIds.has(page.next_page_id)) {
    throw new Error(
      `Repeated page id while fetching OpenHands models: ${page.next_page_id}`,
    );
  }
  seenPageIds.add(page.next_page_id);

  const rest = await fetchAllOpenHandsModels(
    page.next_page_id,
    seenPageIds,
    depth + 1,
  );
  return [...page.items, ...rest];
}

/**
 * Fetches the `openhands` provider's models with their DB-driven `free` /
 * `default` flags (the same channel that carries `verified`). Scoped by the
 * active organization so multi-tenant installs keep per-org catalogs apart.
 */
const useOpenHandsModels = () => {
  const { organizationId } = useOrgTypeAndAccess();

  return useQuery({
    queryKey: [
      "config",
      "models",
      OPENHANDS_PROVIDER,
      "flags",
      organizationId ?? null,
    ],
    queryFn: async () => fetchAllOpenHandsModels(null, new Set(), 0),
    staleTime: 1000 * 60 * 5,
    gcTime: 1000 * 60 * 15,
  });
};

/**
 * Fetches the DB-driven free / default flags once and mirrors them into the
 * {@link useFreeModelsStore}. Mount this high in the tree (inside the query
 * provider). Leaf display components then read the flags synchronously via the
 * {@link useFreeModels} / {@link useDefaultModel} zustand selectors, so they
 * stay renderable in isolation without a QueryClientProvider in scope.
 */
export const useHydrateFreeModels = (): void => {
  const { organizationId } = useOrgTypeAndAccess();
  const { data, isError } = useOpenHandsModels();
  const setFlags = useFreeModelsStore((state) => state.setFlags);
  const markDefaultModelReady = useFreeModelsStore(
    (state) => state.markDefaultModelReady,
  );
  const resetFlags = useFreeModelsStore((state) => state.resetFlags);

  React.useEffect(() => {
    resetFlags();
  }, [organizationId, resetFlags]);

  React.useEffect(() => {
    if (!data) return;
    const freeModels: FreeModelSet = new Set(
      data
        .filter((model) => model.free)
        .map((model) => `${OPENHANDS_PROVIDER}/${model.name}`),
    );
    const defaultEntry = data.find((model) => model.default);
    setFlags({
      freeModels,
      defaultModel: defaultEntry
        ? `${OPENHANDS_PROVIDER}/${defaultEntry.name}`
        : null,
    });
  }, [data, setFlags]);

  React.useEffect(() => {
    if (isError) markDefaultModelReady();
  }, [isError, markDefaultModelReady]);
};

/**
 * Set of free ``openhands/<model>`` ids, sourced from the backend model list
 * (DB-driven on cloud via the `free` flag). Returns an empty set on backends
 * without free metadata (e.g. a self-hosted install), so callers uniformly
 * treat "not in set" as paid and the frontend keeps no hardcoded free list.
 */
export const useFreeModels = (): FreeModelSet =>
  useFreeModelsStore((state) => state.freeModels);

/**
 * DB-driven default OpenHands model id (``openhands/<model>``), used to
 * preselect a model on onboarding and when creating a new OpenHands model.
 * Returns `null` on backends without default metadata.
 */
export const useDefaultModel = (): string | null =>
  useFreeModelsStore((state) => state.defaultModel);

export const useDefaultModelReady = (): boolean =>
  useFreeModelsStore((state) => state.defaultModelReady);
