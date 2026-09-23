import { Navigate, useParams } from "react-router";
import {
  getLegacyResolver,
  isLegacyResolverId,
} from "#/components/features/integrations-hub/legacy-resolvers";
import { INTEGRATIONS_HUB_PATHS } from "#/components/features/integrations-hub/integrations-hub-paths";

/**
 * Deep links to a specific resolver redirect to the Resolvers list.
 * Configuration opens from that page in a modal.
 */
export function LegacyResolverDetailPage() {
  const { providerId } = useParams<{ providerId: string }>();

  if (!isLegacyResolverId(providerId) || !getLegacyResolver(providerId)) {
    return <Navigate to={INTEGRATIONS_HUB_PATHS.resolvers} replace />;
  }

  return (
    <Navigate
      to={INTEGRATIONS_HUB_PATHS.resolvers}
      replace
      state={{ openResolver: providerId }}
    />
  );
}
