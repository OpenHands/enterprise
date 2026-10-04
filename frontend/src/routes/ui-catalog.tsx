/* eslint-disable i18next/no-literal-string */
import { ArrowUpRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router";
import { isSetupTestHarnessEnabled } from "#/utils/org/setup-test-harness";
import { catalogText } from "./ui-catalog-copy";

const SOURCE_WIDTH = 1280;
const SOURCE_HEIGHT = 800;

type ReviewArea =
  | "Access"
  | "Provisioning"
  | "Org status"
  | "Membership"
  | "First-install NUX"
  | "Dashboard";

type CatalogEntry = {
  title: string;
  kind: "Page" | "Modal";
  /** Full-size destination. Pages keep the real route and skip install redirects. */
  src: string;
  description: string;
  /** New behavior from this branch, shown under the preview. */
  features: string[];
  /** What an engineer should check on this screen. */
  notes: string;
  /** Set when this screen is in a major area the review says to check before visual polish. */
  review?: ReviewArea;
};

const PAGE = (
  title: string,
  path: string,
  description: string,
  features: string[],
  notes: string,
  review?: ReviewArea,
): CatalogEntry => ({
  title,
  kind: "Page",
  src: `${path}?catalogPreview=1`,
  description,
  features,
  notes,
  review,
});

const MODAL = (
  title: string,
  id: string,
  description: string,
  features: string[],
  notes: string,
  review?: ReviewArea,
): CatalogEntry => ({
  title,
  kind: "Modal",
  src: `/ui-catalog/frame/${id}`,
  description,
  features,
  notes,
  review,
});

/** Defining file for each catalog card, keyed by route or modal frame id. */
const SOURCE: Record<string, string> = {
  "/install": "frontend/src/routes/super-admin-install-welcome.tsx",
  "/install/tos": "frontend/src/routes/super-admin-install-tos.tsx",
  "/install/account": "frontend/src/routes/super-admin-install-account.tsx",
  "/install/company": "frontend/src/routes/super-admin-install-company.tsx",
  "/install/org": "frontend/src/routes/super-admin-install-org.tsx",
  "/settings/org-defaults": "frontend/src/routes/llm-settings.tsx",
  starter:
    "frontend/src/components/features/super-admin/super-admin-group-setup-modal.tsx",
  "/super-admin/setup":
    "frontend/src/components/features/super-admin/super-admin-setup-guide.tsx",
  "remove-setup":
    "frontend/src/components/features/super-admin/super-admin-setup-guide.tsx",
  "/settings/integrations":
    "frontend/src/components/features/settings/integrations/legacy-resolvers-page.tsx",
  integration:
    "frontend/src/components/features/settings/integrations/integration-modal.tsx",
  "/settings/mcp": "frontend/src/routes/mcp-settings.tsx",
  "mcp-add":
    "frontend/src/components/features/settings/mcp-settings/mcp-server-modal.tsx",
  "mcp-delete": "frontend/src/routes/mcp-settings.tsx",
  "/settings/org-members":
    "frontend/src/routes/manage-organization-members.tsx",
  invite:
    "frontend/src/components/features/org/invite-organization-member-modal.tsx",
  "change-role":
    "frontend/src/components/features/org/confirm-update-role-modal.tsx",
  "remove-member":
    "frontend/src/components/features/org/confirm-remove-member-modal.tsx",
  "/super-admin":
    "frontend/src/components/features/super-admin/super-admin-dashboard.tsx",
  "/super-admin/organizations":
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  "create-org":
    "frontend/src/components/features/org/create-organization-modal.tsx",
  "/super-admin/users":
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  provision:
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  "provision-credentials":
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  "manage-user":
    "frontend/src/components/features/super-admin/super-admin-user-groups-modal.tsx",
  "grant-self":
    "frontend/src/components/features/super-admin/super-admin-grant-self-access-modal.tsx",
  "/super-admin/admins":
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  "grant-admin":
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  "/super-admin/instance":
    "frontend/src/components/features/super-admin/super-admin-pages.tsx",
  "/settings/usage-monitoring":
    "frontend/src/components/features/admin-dashboard/admin-dashboard.tsx",
  "/settings/budgets": "frontend/src/components/features/budgets/budgets.tsx",
  "/settings/org-defaults/condenser":
    "frontend/src/routes/org-default-condenser-settings.tsx",
  "/settings/org-defaults/verification":
    "frontend/src/routes/verification-settings.tsx",
  "/settings/credits": "frontend/src/routes/credits.tsx",
  "/settings/org": "frontend/src/routes/manage-org.tsx",
  "rename-org":
    "frontend/src/components/features/org/change-org-name-modal.tsx",
  "delete-org":
    "frontend/src/components/features/org/delete-org-confirmation-modal.tsx",
  "/settings/agent": "frontend/src/routes/agent-settings.tsx",
  "/settings/api-keys":
    "frontend/src/components/features/settings/api-keys-manager.tsx",
  "/settings/secrets": "frontend/src/routes/secrets-settings.tsx",
  "/settings/skills": "frontend/src/routes/skills-settings.tsx",
};

function sourceFile(src: string): string {
  const key = src.replace(/\?.*$/, "").replace(/^\/ui-catalog\/frame\//, "");
  return SOURCE[key] ?? src;
}

const CATALOG_LANGUAGES = [
  { label: "English", value: "en" },
  { label: "简体中文", value: "zh-CN" },
  { label: "繁體中文", value: "zh-TW" },
] as const;

const CATALOG_LANGUAGE_KEY = "oh-catalog-lng";

function readCatalogLanguage(): string {
  if (typeof window === "undefined") {
    return "en";
  }
  const stored = window.localStorage.getItem(CATALOG_LANGUAGE_KEY);
  return CATALOG_LANGUAGES.some((language) => language.value === stored)
    ? stored!
    : "en";
}

const NEO_SETTINGS = [
  "OpenHands-Neo settings chrome, form controls, and toasts",
  "Settings nav brand reads Account",
];

type CatalogGroup = {
  ticket: string;
  title: string;
  flow: string;
  entries: CatalogEntry[];
};

const GROUPS: CatalogGroup[] = [
  {
    ticket: "OHE-3384 · PRD OHE-651",
    title: "First-time Super Admin onboarding",
    flow: "Install wizard, in order: welcome, terms, account, company, first organization, then org LLM and the starter modal. The acceptance criteria also say the organization is created in the background with the Super Admin as owner. This wizard still asks them to name it. The license key stays in this browser. Unit tests, the feature flag, the OpenHands-Cloud E2E, and the bug bash are not screens on this page.",
    entries: [
      PAGE(
        "Welcome",
        "/install",
        "Opens the Super Admin install. Progress is stored in this browser only.",
        [
          "Seven-step install bar from welcome through first automation",
          "Animated OpenHands mark and welcome copy",
          "Next marks this step done in this browser",
        ],
        "Confirm Next only writes the oh-sa-nux welcome flag in this browser. A finished install must not return here unless that storage is cleared.",
        "First-install NUX",
      ),
      PAGE(
        "Terms",
        "/install/tos",
        "Terms acceptance before account setup. Clearing storage starts the flow over.",
        [
          "Terms must be accepted before the account step",
          "Acceptance is stored in this browser, not on the server",
          "Clearing storage restarts the install",
        ],
        "Acceptance has to block the account step. There is no server record of it. Clearing oh-sa-nux must restart the flow.",
        "First-install NUX",
      ),
      PAGE(
        "Account",
        "/install/account",
        "Installer name and email. This step does not create the server account by itself.",
        [
          "Full name, email, and password for the installing Super Admin",
          "Does not create the server account by itself",
          "Progress stays in this browser",
        ],
        "This form must not create a user or send the password to an API. The name is one field, not a stored first and last name.",
        "First-install NUX",
      ),
      PAGE(
        "Company",
        "/install/company",
        "Company name, logo, and a license key or evaluation trial. The key does not activate a license. The name and logo are saved to the server.",
        [
          "Company name and logo",
          "License key or evaluation trial",
          "The key does not activate a license, and the name and logo are saved to the server",
        ],
        "Treat the license key as incomplete: it is a local choice, not license activation. Confirm the name and logo are saved and the logo shows for other users.",
        "First-install NUX",
      ),
      PAGE(
        "Organization",
        "/install/org",
        "Names the first organization, then continues to LLM settings.",
        [
          "Names the first organization on the instance",
          "Continues to LLM settings",
          "Leaves the starter modal pending",
        ],
        "This should run only after welcome, terms, account, and company are done, then open LLM settings with the starter modal pending. It must not skip to the dashboard.",
        "First-install NUX",
      ),
      PAGE(
        "Language Model",
        "/settings/org-defaults",
        "Org LLM defaults. After first install, the starter modal opens on this page.",
        [
          "Last step of first install opens the starter modal here",
          "LLM profile rows no longer clip on small screens",
          ...NEO_SETTINGS,
        ],
        "The starter modal should open only while starterModalPending is set, and closing it must clear that flag. Check the profile rows at a narrow width.",
        "First-install NUX",
      ),
      MODAL(
        "Starter setup",
        "starter",
        "After install, add an LLM and an integration. Add an LLM starts open. Skip closes the modal.",
        [
          "Add an LLM starts open",
          "The pinned step stays open while another step is hovered",
          "Models are checkboxes in a dropdown, including All",
          "Skip dismisses the modal",
        ],
        "Skip must clear starterModalPending. Hovering another step must not collapse the pinned one. The catalog forceOpen path must not clear a real user's nux.",
        "First-install NUX",
      ),
    ],
  },
  {
    ticket: "OHE-3384 · PRD OHE-651",
    title: "Onboarding guide to the aha moment",
    flow: "After install, the setup guide follows the Super Admin across the product: organization, LLM, integration, automation, invite, then optional SAML. It is for the first organization they own. A new admin of an organization that is already configured should not see it. There is no automation-template screen here; the guide links to /automations. Guide progress is local storage, not a server flag, so it does not yet know that an org is fully configured.",
    entries: [
      PAGE(
        "Setup guide",
        "/super-admin/setup",
        "Checklist after install. Dismissing it hides the guide.",
        [
          "Steps for org, LLM, integration, automation, invite, and optional SAML",
          "The active step stays open while another step is previewed",
          "The guide can be dismissed",
        ],
        "Confirm a hovered step does not replace the pinned step. Dismiss should hide the guide without clearing oh-sa-nux, and Start should bring it back.",
      ),
      MODAL(
        "Remove setup guide",
        "remove-setup",
        "Confirms hiding the setup guide.",
        ["Confirms hiding the setup guide", "The guide can be started again"],
        "Dismiss should only flip the local setup-guide flag. It must not clear oh-sa-nux or org data.",
      ),
      PAGE(
        "Integrations",
        "/settings/integrations",
        "Git and project resolvers. Hub addresses redirect here. The Integrations Hub is not on this branch.",
        [
          "Personal Integrations is the resolver list",
          "GitHub, GitLab, Jira, Linear, Slack, and the other git providers",
          "The Integrations Hub is removed; old hub addresses redirect here",
          ...NEO_SETTINGS,
        ],
        "Confirm hub routes redirect here and ENABLE_INTEGRATIONS_HUB is not on this branch. Resolver connect should still work for each provider.",
      ),
      MODAL(
        "Configure integration",
        "integration",
        "Connect a provider with a token or webhook.",
        [
          "Token or webhook form for the selected provider",
          "The Integrations Hub connect flow is not on this branch",
        ],
        "This is the legacy resolver form. Confirm the token or webhook is not shown again after save, and that no hub connector is mounted.",
      ),
      PAGE(
        "MCP",
        "/settings/mcp",
        "Model Context Protocol servers. The label is a display name; the saved config key stays as entered.",
        [
          "Servers render as rows, like Integrations",
          "Display names capitalize and drop hyphens; the saved config key is unchanged",
          "Known servers use Integrations marks and Simple Icons",
          ...NEO_SETTINGS,
        ],
        "Confirm the display name is not written back as the config key. A short name can match the wrong logo because matching is a substring.",
      ),
      MODAL(
        "Add MCP server",
        "mcp-add",
        "Add or edit a server. The saved name is the config key.",
        [
          "One dialog adds a server and edits an existing one",
          "The saved name is the config key",
        ],
        "Add and edit share this dialog. Confirm the saved key is the name field, not the capitalized display label.",
      ),
      MODAL(
        "Delete MCP server",
        "mcp-delete",
        "Confirms removing a server.",
        [
          "Confirms removal of the server",
          "The config key is what gets deleted",
        ],
        "Confirm deletion removes the stored config key, even when the row shows a display name.",
      ),
      PAGE(
        "Members",
        "/settings/org-members",
        "Email and role for people in this organization. First and last name are not stored.",
        [
          "Invite, role change, and removal stay on email and role",
          "First and last name are not stored, so they are not shown",
          "Setup guide invite step links here",
          ...NEO_SETTINGS,
        ],
        "Do not expect a first or last name. Invite, role change, and remove still use email and role. Removal must keep the last-owner rule.",
      ),
      MODAL(
        "Invite members",
        "invite",
        "Invite people by email and role into the current organization.",
        ["Invite by email and role", "Restyled with the neo modal chrome"],
        "Existing membership action. Confirm the neo dialog still submits email and role, and does not ask for a name.",
      ),
      MODAL(
        "Change member role",
        "change-role",
        "Confirms the new role for someone in the current organization.",
        ["Confirms the new role", "Restyled with the neo modal chrome"],
        "Existing confirm. The submitted role should be the one shown, including owner, admin, and member.",
      ),
      MODAL(
        "Remove member",
        "remove-member",
        "Confirms removing someone from the current organization.",
        [
          "Confirms removal from this organization",
          "Restyled with the neo modal chrome",
        ],
        "Existing confirm. Removal must still refuse to drop the last owner.",
      ),
    ],
  },
  {
    ticket: "OHE-3433 · PRD OHE-651",
    title: "Super Admin dashboard",
    flow: "Opened from the org menu in settings when the viewer is a Super Admin. Then create organizations, grant and revoke Super Admins, provision and manage users, and edit org membership. Delete and disable live on Manage user. SSO is not configurable here, and Instance Settings has no documentation link for the SSO environment variable. Email and auto-org on that page are read-only. The gate is ENABLE_SUPER_ADMIN, accepting true or 1. The OpenHands-Cloud E2E and the bug bash are still outside this UI.",
    entries: [
      PAGE(
        "Dashboard",
        "/super-admin",
        "Instance overview. A normal org member must not open this route.",
        [
          "Gated by ENABLE_SUPER_ADMIN, accepting true or 1",
          "Instance overview with the next setup-guide step",
          "Conversation list and stop actions",
          "Org menu pins Super Admin with a yellow shield",
        ],
        "Confirm ENABLE_SUPER_ADMIN accepts both true and 1, and a normal org member is denied. Review empty, error, and stop-conversation states more than layout. The shield belongs only on /super-admin.",
        "Dashboard",
      ),
      PAGE(
        "Organizations",
        "/super-admin/organizations",
        "List, create, open, suspend, and resume organizations. Existing orgs default to active.",
        [
          "Create, open, suspend, and resume organizations",
          "Suspend blocks usage and can be reversed",
          "Opening an org you are not in asks you to join first",
          "Existing organizations default to active",
        ],
        "Review migration 144. Existing orgs must default to active. Suspend has to block usage, resume has to undo it, and callers must not ignore org.status.",
        "Org status",
      ),
      MODAL(
        "Create organization",
        "create-org",
        "Name, owner, and contact email for a new organization.",
        [
          "Opened from the org menu and from Super Admin organizations",
          "Sets the name, owner, and contact email",
        ],
        "Confirm the owner and contact email are the values that get stored, and the org is created active.",
        "Membership",
      ),
      PAGE(
        "Users",
        "/super-admin/users",
        "Everyone on the instance. Provision a user and edit membership across organizations.",
        [
          "Provision a user into one or more organizations",
          "Role is chosen per organization",
          "Manage that person's access across organizations",
          "Password and API key are shown after provision",
        ],
        "Confirm only a Super Admin can provision, the role is stored per org, and the returned API key is not logged. Cross-org writes go through the membership service.",
        "Provisioning",
      ),
      MODAL(
        "Provision User",
        "provision",
        "Creates a user in one or more organizations and can return a password and API key.",
        [
          "Create a user in one or more organizations",
          "Role is chosen per organization",
          "Can return a password and an API key",
        ],
        "Confirm who can call provision, that each selected org stores its own role, and that a failed call does not show credentials.",
        "Provisioning",
      ),
      MODAL(
        "Provision credentials",
        "provision-credentials",
        "Shows the new password and LiteLLM API key in plain text. Confirm those values are not logged.",
        [
          "Shows the new password in plain text",
          "Shows the LiteLLM API key in plain text",
          "Copy to clipboard",
        ],
        "The LiteLLM key is plain text. Confirm it is not logged or written to localStorage, and that closing the dialog removes it from the page.",
        "Provisioning",
      ),
      MODAL(
        "Manage user",
        "manage-user",
        "Membership and role for one person across organizations. Watch the last-owner rule.",
        [
          "Org access and role for one person",
          "Add or change membership in specific organizations",
          "Last-owner rule still applies",
        ],
        "Review cross-org add, remove, and role changes. A Super Admin must not remove the last owner of an org.",
        "Membership",
      ),
      MODAL(
        "Grant yourself access",
        "grant-self",
        "A Super Admin picks a role and joins an organization before opening it.",
        [
          "A Super Admin must join an organization before opening it",
          "The role is chosen at join time",
        ],
        "Opening an org without membership must stay blocked until this completes, and the role written must be the one selected.",
        "Membership",
      ),
      PAGE(
        "Super Admins",
        "/super-admin/admins",
        "Who holds the instance role. Grant and revoke require manage_super_admins.",
        [
          "Lists who holds the instance Super Admin role",
          "Grant the role by email",
          "Revoke requires manage_super_admins",
          "A normal org member must not reach this page",
        ],
        "Grant and revoke must require manage_super_admins. Confirm this route cannot bypass the Super Admin access check, and an org admin cannot open it.",
        "Access",
      ),
      MODAL(
        "Grant Super Admin",
        "grant-admin",
        "Gives the instance Super Admin role to an email. A normal org member must not be able to do this.",
        [
          "Grants the instance Super Admin role by email",
          "Requires manage_super_admins",
        ],
        "A caller without manage_super_admins must be rejected. Confirm granting does not also skip org membership checks for that user.",
        "Access",
      ),
      PAGE(
        "Instance Settings",
        "/super-admin/instance",
        "Instance logo and email status. Check the permission-denied state, not the layout.",
        [
          "Instance logo",
          "Email delivery status",
          "Optional SAML setup step links here",
        ],
        "Review the permission-denied and error states. Logo and email status must stay behind Super Admin access.",
        "Dashboard",
      ),
    ],
  },
  {
    ticket: "Settings chrome · this branch",
    title: "Neo theme on the screens these flows land in",
    flow: "Not its own product ticket. Visual pass on the settings screens around the two sprints. Review layout and save behavior, not access or provisioning.",
    entries: [
      PAGE(
        "Dashboard",
        "/settings/usage-monitoring",
        "Usage for the current organization.",
        [
          "Usage dashboard restyled with neo theme tokens",
          "Loading skeletons match the neo chrome",
          ...NEO_SETTINGS,
        ],
        "Visual regression on the usage widgets and skeletons. No access or billing logic changed on this page.",
      ),
      PAGE(
        "Budgets",
        "/settings/budgets",
        "Spending limits and alerts for the organization.",
        ["Budgets restyled with neo theme tokens", ...NEO_SETTINGS],
        "Visual regression on budget tabs and alerts. Confirm limits still save the same way.",
      ),
      PAGE(
        "Condenser",
        "/settings/org-defaults/condenser",
        "How conversation history is condensed for the organization.",
        [
          "Grouped under Language Model in the reordered settings nav",
          ...NEO_SETTINGS,
        ],
        "Visual and nav-order check. Confirm condenser settings still save, and the ACP disable still applies if it did before.",
      ),
      PAGE(
        "Verification",
        "/settings/org-defaults/verification",
        "Checks that the organization LLM setup works.",
        [
          "Grouped under Language Model in the reordered settings nav",
          ...NEO_SETTINGS,
        ],
        "Visual and nav-order check. Confirm verification still runs against the org LLM setup.",
      ),
      PAGE(
        "Billing & Credits",
        "/settings/credits",
        "Plan, credits, and payment for the organization.",
        ["Billing restyled with neo theme tokens", ...NEO_SETTINGS],
        "Visual pass. This is not the provisioned LiteLLM key. Confirm plan and credit actions still submit.",
      ),
      PAGE(
        "Organization",
        "/settings/org",
        "Name and deletion for the current organization.",
        [
          "Create organization is available from the org menu",
          "Rename and delete stay on this organization",
          ...NEO_SETTINGS,
        ],
        "Confirm Create organization in the org menu does not break org switching. Delete here is the current org, not Super Admin suspend.",
      ),
      MODAL(
        "Change organization name",
        "rename-org",
        "Renames the current organization.",
        [
          "Renames the current organization",
          "Restyled with the neo modal chrome",
        ],
        "Existing rename. Confirm the saved name is the field value and the org menu updates after save.",
      ),
      MODAL(
        "Delete organization",
        "delete-org",
        "Confirms deleting the current organization.",
        [
          "Confirms deletion of the current organization",
          "Restyled with the neo modal chrome",
        ],
        "Existing delete. This is not Super Admin suspend. Confirm it deletes only the current org and still requires the confirm step.",
      ),
      PAGE(
        "Agent",
        "/settings/agent",
        "Agent behavior settings for the organization.",
        NEO_SETTINGS,
        "Visual pass on the neo chrome. Confirm agent settings still save with the existing form.",
      ),
      PAGE(
        "API Keys",
        "/settings/api-keys",
        "Keys the organization uses to call OpenHands.",
        NEO_SETTINGS,
        "Visual pass. Keys should still save immediately. This is separate from the LiteLLM key returned by provisioning.",
      ),
      PAGE(
        "Secrets",
        "/settings/secrets",
        "Named secrets available to conversations.",
        NEO_SETTINGS,
        "Visual pass. Adding and deleting a secret should still hit the secrets API immediately, with values masked after save.",
      ),
      PAGE(
        "Skills",
        "/settings/skills",
        "Organization skills the agent can load.",
        NEO_SETTINGS,
        "Visual pass. Confirm adding and removing a skill still saves immediately.",
      ),
    ],
  },
];

const MAX_LIVE_FRAMES = 6;
let liveFrames = 0;
const frameWaiters: Array<() => void> = [];

function acquireFrame(): Promise<() => void> {
  return new Promise((resolve) => {
    const grant = () => {
      liveFrames += 1;
      let released = false;
      resolve(() => {
        if (released) {
          return;
        }
        released = true;
        liveFrames -= 1;
        frameWaiters.shift()?.();
      });
    };
    if (liveFrames < MAX_LIVE_FRAMES) {
      grant();
      return;
    }
    frameWaiters.push(grant);
  });
}

function ScaledFrame({ src, title }: { src: string; title: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [visible, setVisible] = useState(false);
  const [live, setLive] = useState(false);

  useEffect(() => {
    const node = hostRef.current;
    if (!node) {
      return undefined;
    }
    const measure = () => {
      if (node.clientWidth > 0) {
        setWidth(node.clientWidth);
      }
    };
    measure();
    const resizeObserver = new ResizeObserver(measure);
    resizeObserver.observe(node);
    const intersectionObserver = new IntersectionObserver(
      ([entry]) => setVisible(Boolean(entry?.isIntersecting)),
      { rootMargin: "80px" },
    );
    intersectionObserver.observe(node);
    return () => {
      resizeObserver.disconnect();
      intersectionObserver.disconnect();
    };
  }, []);

  useEffect(() => {
    if (!visible || width <= 0) {
      return undefined;
    }
    let cancelled = false;
    let release: (() => void) | undefined;
    acquireFrame().then((done) => {
      if (cancelled) {
        done();
        return;
      }
      release = done;
      setLive(true);
    });
    return () => {
      cancelled = true;
      setLive(false);
      release?.();
    };
  }, [visible, width]);

  const scale = width > 0 ? width / SOURCE_WIDTH : 1;

  return (
    <div
      ref={hostRef}
      className="w-full overflow-hidden rounded-lg border border-[var(--oh-border)] bg-base"
      style={{ height: width > 0 ? SOURCE_HEIGHT * scale : 220 }}
    >
      {live ? (
        <div
          style={{
            width: SOURCE_WIDTH,
            height: SOURCE_HEIGHT,
            transform: `scale(${scale})`,
            transformOrigin: "top left",
          }}
        >
          <iframe
            key={src}
            title={title}
            src={src}
            tabIndex={-1}
            className="pointer-events-none border-0 bg-base"
            style={{ width: SOURCE_WIDTH, height: SOURCE_HEIGHT }}
          />
        </div>
      ) : null}
    </div>
  );
}

export default function UiCatalog() {
  const [language, setLanguage] = useState(readCatalogLanguage);

  const chooseLanguage = (next: string) => {
    window.localStorage.setItem(CATALOG_LANGUAGE_KEY, next);
    setLanguage(next);
  };
  const text = (value: string) => catalogText(language, value);

  if (!isSetupTestHarnessEnabled()) {
    return <Navigate to="/" replace />;
  }

  return (
    <main className="min-h-screen bg-base text-white">
      <div className="sticky top-0 z-30 border-b border-[var(--oh-border)] bg-base">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <p className="text-sm font-medium">{text("Language")}</p>
          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label={text("Language")}
          >
            {CATALOG_LANGUAGES.map((item) => {
              const selected = item.value === language;
              return (
                <button
                  key={item.value}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => chooseLanguage(item.value)}
                  className={`inline-flex items-center rounded-lg border px-2.5 py-1 text-xs font-medium ${
                    selected
                      ? "border-white bg-white text-black"
                      : "border-[var(--oh-border)] bg-base-secondary text-white hover:bg-surface-raised"
                  }`}
                >
                  {item.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
      <div className="mx-auto flex max-w-6xl flex-col gap-10 px-6 py-8">
        <header className="flex flex-col gap-2">
          <p className="text-xs font-medium uppercase tracking-wide text-[var(--oh-muted)]">
            {text("Mock only")}
          </p>
          <h1 className="text-2xl font-semibold">{text("Pages and modals")}</h1>
          <p className="max-w-3xl text-sm leading-5 text-[var(--oh-muted)]">
            {text(
              "Cards are batched by sprint and shown in flow order. View opens that exact screen. Needs review marks access, provisioning, org status, membership, first install, and the Super Admin dashboard.",
            )}
          </p>
        </header>
        {GROUPS.map((group) => (
          <section key={group.title} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1">
              <p className="text-xs font-medium uppercase tracking-wide text-[var(--oh-muted)]">
                {text(group.ticket)}
              </p>
              <h2 className="text-lg font-medium">{text(group.title)}</h2>
              <p className="max-w-3xl text-sm leading-5 text-[var(--oh-muted)]">
                {text(group.flow)}
              </p>
            </div>
            <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
              {group.entries.map((entry) => (
                <article key={entry.src} className="flex flex-col gap-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="truncate text-sm font-medium">
                          {text(entry.title)}
                        </h3>
                        {entry.review ? (
                          <span className="shrink-0 rounded border border-danger px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-danger">
                            {text("Needs review")} · {text(entry.review)}
                          </span>
                        ) : null}
                      </div>
                      <p className="text-xs leading-4 text-[var(--oh-muted)]">
                        {text(entry.description)}
                      </p>
                      <p className="break-all font-mono text-[11px] leading-4 text-[var(--oh-muted)]">
                        {sourceFile(entry.src)}
                      </p>
                    </div>
                    <Link
                      to={entry.src}
                      aria-label={`${text("View")} ${text(entry.title)}`}
                      className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--oh-border)] bg-base-secondary px-2.5 py-1 text-xs font-medium text-white hover:bg-surface-raised"
                    >
                      <ArrowUpRight className="size-3.5" aria-hidden />
                      {text("View")}
                    </Link>
                  </div>
                  <ScaledFrame src={entry.src} title={text(entry.title)} />
                  <ul className="list-disc space-y-1 pl-4 text-xs leading-4 text-[var(--oh-muted)]">
                    {entry.features.map((feature) => (
                      <li key={feature}>{text(feature)}</li>
                    ))}
                  </ul>
                  <p className="border-l border-[var(--oh-border)] pl-3 text-xs leading-4 text-[var(--oh-muted)]">
                    <span className="font-medium text-white">
                      {text("Review.")}{" "}
                    </span>
                    {text(entry.notes)}
                  </p>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
