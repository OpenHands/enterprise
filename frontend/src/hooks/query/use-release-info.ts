import { useQuery } from "@tanstack/react-query";
import OptionService from "#/api/option-service/option-service.api";
import { QUERY_KEYS, CONFIG_CACHE_OPTIONS } from "./query-keys";

/**
 * Fetch the authenticated ``/api/v1/release`` endpoint once per session
 * (cached like other static config) so an About surface can show the
 * deployed Enterprise release and agent SDK versions. A single on-demand
 * request, not polling.
 *
 * Fails gracefully (``isError``) on older servers without this route or on
 * network errors — callers should render an "unavailable" state rather than
 * blocking on it, so the global query-error toast is disabled here.
 */
export const useReleaseInfo = () =>
  useQuery({
    queryKey: QUERY_KEYS.RELEASE_INFO,
    queryFn: OptionService.getReleaseInfo,
    ...CONFIG_CACHE_OPTIONS,
    retry: 1,
    meta: { disableToast: true },
  });
