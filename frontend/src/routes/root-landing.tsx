import React from "react";
import { redirect } from "react-router";
import { Route } from "./+types/root-landing";
import { getAgentCanvasBannerLink } from "#/components/features/home/home-header/agent-canvas-banner";
import { LoadingSpinner } from "#/components/shared/loading-spinner";

/**
 * Invitation links land on `/?invitation_token=…` (see
 * `accept_invitation_redirect` in `org_invitations.py`). Keep them in the
 * settings shell, where `root.tsx` captures the token and `root-layout`
 * auto-accepts it, instead of leaving for Canvas before that happens.
 */
export function clientLoader({ request }: Route.ClientLoaderArgs) {
  const { search, searchParams } = new URL(request.url);
  if (searchParams.has("invitation_token")) {
    return redirect(`/settings${search}`);
  }
  return null;
}

/**
 * Agent Canvas (served at `${origin}/canvas` next to this app) is the primary
 * surface, so `/` — and therefore the default post-login destination — hard
 * redirects there. `location.replace` keeps `/` out of history; a router
 * redirect would bounce through the SPA's own `canvas/*` route instead.
 */
export default function RootLanding() {
  React.useEffect(() => {
    window.location.replace(getAgentCanvasBannerLink(window.location).url);
  }, []);

  return (
    <div className="flex h-full items-center justify-center">
      <LoadingSpinner size="large" />
    </div>
  );
}
