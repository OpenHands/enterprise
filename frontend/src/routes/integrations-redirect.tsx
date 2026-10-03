import { Navigate, useLocation, useParams } from "react-router";
import { isLegacyResolverId } from "#/components/features/settings/integrations/legacy-resolvers";

/** Old Integrations Hub resolver URLs land on personal Integrations. */
export default function IntegrationsRedirect() {
  const params = useParams();
  const location = useLocation();
  const splat = params["*"] ?? "";
  const fromPath = splat.match(/^resolvers\/([^/]+)/)?.[1];
  const fromQuery = new URLSearchParams(location.search).get("resolver");
  const resolver = fromPath ?? fromQuery ?? undefined;

  return (
    <Navigate
      to="/settings/integrations"
      replace
      state={
        isLegacyResolverId(resolver) ? { openResolver: resolver } : undefined
      }
    />
  );
}
