import {
  type RouteConfig,
  layout,
  index,
  route,
} from "@react-router/dev/routes";

export default [
  route("login", "routes/login.tsx"),
  route("onboarding", "routes/onboarding-form.tsx"),
  route("information-request", "routes/information-request.tsx"),
  route("automations/*", "routes/automations-redirect.tsx"),
  route("canvas/*", "routes/cross-app-redirect.tsx", {
    id: "routes/canvas-cross-app-redirect",
  }),
  route("integrations-hub/*", "routes/cross-app-redirect.tsx", {
    id: "routes/integrations-hub-cross-app-redirect",
  }),
  layout("routes/root-layout.tsx", [
    index("routes/root-to-settings.tsx"),
    route("accept-tos", "routes/accept-tos.tsx"),
    route("launch", "routes/launch.tsx"),
    route("settings", "routes/settings.tsx", [
      index("routes/llm-settings.tsx"),
      route("agent", "routes/agent-settings.tsx"),
      route("condenser", "routes/condenser-settings.tsx"),
      route("verification", "routes/verification-settings.tsx"),
      route("org-defaults", "routes/org-default-llm-settings.tsx"),
      route(
        "org-defaults/condenser",
        "routes/org-default-condenser-settings.tsx",
      ),
      route(
        "org-defaults/verification",
        "routes/org-default-verification-settings.tsx",
      ),
      route("mcp", "routes/mcp-settings.tsx"),
      route("skills", "routes/skills-settings.tsx"),
      route("user", "routes/user-settings.tsx"),
      route("integrations", "routes/personal-integrations-layout.tsx", [
        index("routes/personal-integrations-index.tsx"),
        route("agent-requests", "routes/integrations-hub-agent-requests.tsx", {
          id: "routes/personal-integrations-agent-requests",
        }),
        route(
          "agent-connection",
          "routes/integrations-hub-agent-connection.tsx",
          { id: "routes/personal-integrations-agent-connection" },
        ),
      ]),
      route("integrations-hub", "routes/integrations-hub-layout.tsx", [
        index("routes/integrations-hub.tsx"),
        route("agent-requests", "routes/integrations-hub-agent-requests.tsx", {
          id: "routes/integrations-hub-legacy-agent-requests",
        }),
        route(
          "agent-connection",
          "routes/integrations-hub-agent-connection.tsx",
          { id: "routes/integrations-hub-legacy-agent-connection" },
        ),
        route("admin-overview", "routes/integrations-hub-admin-overview.tsx"),
        route("admin-catalog", "routes/integrations-hub-admin-catalog.tsx"),
        route(
          "admin-user-requests",
          "routes/integrations-hub-admin-user-requests.tsx",
        ),
        route("resolvers", "routes/integrations-hub-resolvers.tsx"),
        route("resolvers/:providerId", "routes/integrations-hub-resolver.tsx"),
      ]),
      route("app", "routes/app-settings.tsx"),
      route("billing", "routes/billing.tsx"),
      route("credits", "routes/credits.tsx"),
      route("secrets", "routes/secrets-settings.tsx"),
      route("api-keys", "routes/api-keys.tsx"),
      route("org-members", "routes/manage-organization-members.tsx"),
      route("org", "routes/manage-org.tsx"),
      route("usage-monitoring", "routes/usage-monitoring.tsx"),
      route("admin-dashboard", "routes/admin-dashboard.tsx"),
      route("budgets", "routes/budgets.tsx"),
    ]),
    route("super-admin", "routes/super-admin.tsx", [
      index("routes/super-admin-overview.tsx"),
      route("setup", "routes/super-admin-setup.tsx"),
      route("organizations", "routes/super-admin-organizations.tsx"),
      route("users", "routes/super-admin-users.tsx"),
      route("admins", "routes/super-admin-admins.tsx"),
      route("usage", "routes/super-admin-usage.tsx"),
      route("instance", "routes/super-admin-instance.tsx"),
    ]),
    route("conversations/:conversationId", "routes/conversation.tsx"),
    route("oauth/device/verify", "routes/device-verify.tsx"),
  ]),
  // Shared routes that don't require authentication
  route(
    "shared/conversations/:conversationId",
    "routes/shared-conversation.tsx",
  ),
] satisfies RouteConfig;
