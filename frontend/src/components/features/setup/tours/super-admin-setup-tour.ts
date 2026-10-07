import type { GuidedTour } from "./types";
import { SUPER_ADMIN_PATHS } from "#/constants/super-admin-nav";

const NAV = {
  orgDefaults: '[data-testid="sidebar-settings-/settings/org-defaults"]',
  members: '[data-testid="sidebar-settings-/settings/org-members"]',
  instance: '[data-testid="sidebar-settings-/super-admin/instance"]',
} as const;

/**
 * Spotlight / hovercard stops for the Super Admin setup checklist.
 * Titles/bodies are English for the tour popover; setup page still uses i18n.
 *
 * Checklist completion is driven by real actions (e.g. LLM saved), not by
 * advancing through these tips.
 *
 * `alsoHighlight` keeps the related left-nav tab lit alongside each stop.
 */
export const SUPER_ADMIN_SETUP_TOUR: GuidedTour = {
  id: "super-admin-setup",
  title: "Setup guide",
  steps: [
    {
      id: "add-llm",
      checklistId: "add-llm",
      route: "/settings/org-defaults",
      anchor: '[data-testid="add-llm-profile"]',
      alsoHighlight: NAV.orgDefaults,
      title: "Add an LLM profile",
      body: "Click Add LLM profile, or press Next to open the configuration form. Org defaults apply to everyone in this organization.",
      side: "left",
      nextClicksAnchor: true,
    },
    {
      id: "add-llm-provider",
      checklistId: "add-llm",
      route: "/settings/org-defaults",
      anchor: '[data-testid="llm-provider-input"]',
      alsoHighlight: NAV.orgDefaults,
      openViaClick: '[data-testid="add-llm-profile"]',
      title: "Choose a provider",
      body: "Select the LLM provider (for example OpenAI, Anthropic, or OpenHands).",
      side: "bottom",
    },
    {
      id: "add-llm-model",
      checklistId: "add-llm",
      route: "/settings/org-defaults",
      anchor: '[data-testid="llm-model-input"]',
      alsoHighlight: NAV.orgDefaults,
      openViaClick: '[data-testid="add-llm-profile"]',
      title: "Choose a model",
      body: "Pick the model this organization should use by default.",
      side: "bottom",
    },
    {
      id: "add-llm-api-key",
      checklistId: "add-llm",
      route: "/settings/org-defaults",
      anchor:
        '[data-testid="llm-api-key-input"], [data-testid="openhands-api-key-help"], [data-testid="llm-settings-form-basic"]',
      alsoHighlight: NAV.orgDefaults,
      openViaClick: '[data-testid="add-llm-profile"]',
      title: "Add an API key",
      body: "Enter the provider API key if prompted. OpenHands-managed models may not need a key — press Next to continue.",
      side: "bottom",
    },
    {
      id: "add-llm-save",
      checklistId: "add-llm",
      route: "/settings/org-defaults",
      anchor: '[data-testid="save-button"]',
      alsoHighlight: NAV.orgDefaults,
      openViaClick: '[data-testid="add-llm-profile"]',
      title: "Save the LLM profile",
      body: "Click Save to store and activate this organization default. The guide continues automatically when it succeeds.",
      side: "top",
      waitForComplete: "add-llm",
    },
    // "Add an integration" opens Agent Canvas's MCP page, which the tour
    // cannot follow, so it has no stop (like the automation template step).
    {
      id: "invite-users",
      checklistId: "invite-users",
      route: "/settings/org-members",
      anchor:
        '[data-testid="settings-page-subtitle"], [data-testid="settings-screen"], main',
      alsoHighlight: NAV.members,
      title: "Invite users",
      body: "Invite teammates so they can start working in this organization.",
      side: "bottom",
    },
    {
      id: "optional-saml",
      checklistId: "optional-saml",
      route: SUPER_ADMIN_PATHS.instance,
      anchor:
        '[data-testid="super-admin-instance-sso"], [data-testid="super-admin-instance"]',
      alsoHighlight: NAV.instance,
      title: "Optional: SAML / instance",
      body: "Harden the installation with SSO and other instance settings when you are ready.",
      side: "bottom",
    },
  ],
};
