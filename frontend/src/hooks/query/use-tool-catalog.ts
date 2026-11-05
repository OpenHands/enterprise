import { useQuery } from "@tanstack/react-query";
import ToolsService from "#/api/tools-service";

/** The server's tool catalog, for the Agent Profile tools picker. */
export const useToolCatalog = (enabled = true) =>
  useQuery({
    queryKey: ["tool-catalog"],
    queryFn: ToolsService.getToolCatalog,
    enabled,
    staleTime: 1000 * 60 * 10, // the catalog only changes with a deploy
    refetchOnWindowFocus: false,
    meta: { disableToast: true },
  });
