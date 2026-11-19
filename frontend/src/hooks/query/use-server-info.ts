import { useQuery } from "@tanstack/react-query";
import OptionService from "#/api/option-service/option-service.api";
import { QUERY_KEYS, CONFIG_CACHE_OPTIONS } from "./query-keys";

/**
 * Fetch the unauthenticated ``/server_info`` endpoint once per session
 * (cached like other static config) so an About surface can show the
 * deployed Enterprise server and agent SDK versions. A single on-demand
 * request, not polling.
 *
 * Fails gracefully (``isError``) on older servers without this route or on
 * network errors — callers should render an "unavailable" state rather than
 * blocking on it.
 */
export const useServerInfo = () =>
  useQuery({
    queryKey: QUERY_KEYS.SERVER_INFO,
    queryFn: OptionService.getServerInfo,
    ...CONFIG_CACHE_OPTIONS,
    retry: 1,
  });
