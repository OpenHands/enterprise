import {
  AppWindow,
  BarChart2,
  Briefcase,
  DollarSign,
  Shield,
  Users,
} from "lucide-react";
import CreditCardIcon from "#/icons/credit-card.svg?react";
import KeyIcon from "#/icons/key.svg?react";
import MemoryIcon from "#/icons/memory_icon.svg?react";
import RobotIcon from "#/icons/u-robot.svg?react";
import ServerProcessIcon from "#/icons/server-process.svg?react";
import SkillsIcon from "#/icons/skills.svg?react";
import CircuitIcon from "#/icons/u-circuit.svg?react";
import PuzzlePieceIcon from "#/icons/u-puzzle-piece.svg?react";
import UserIcon from "#/icons/user.svg?react";

export type SettingsNavSection = "org" | "personal" | "user" | "billing";

export interface SettingsNavItem {
  icon: React.ReactElement;
  to: string;
  text: string;
  /** Short grey subline under the page title (`settings.tsx`). */
  subtitle: string;
  section?: SettingsNavSection;
  // When true, this item is greyed out (and its route redirects to
  // ``/settings/agent``) while the personal-scope active agent is ACP.
  // The ACP sub-agent manages its own LLM and condenser, so those
  // OpenHands-side surfaces have no useful content. (MCP is intentionally
  // NOT flagged: MCP servers configured here are forwarded to the ACP
  // subprocess at session creation, so the page is meaningful under ACP.)
  // Drives both the navigation disable in ``use-settings-nav-items.ts``
  // and the server-side redirect in ``routes/settings.tsx`` from one source.
  disabledByAcp?: boolean;
}

const ICON_SIZE = 16;

export const SAAS_NAV_ITEMS: SettingsNavItem[] = [
  {
    icon: <Briefcase className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/org",
    text: "SETTINGS$NAV_ORGANIZATION",
    subtitle: "SETTINGS$PAGE_ORGANIZATION_SUBLINE",
    section: "org",
  },
  {
    icon: <Users className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/org-members",
    text: "SETTINGS$NAV_ORG_MEMBERS",
    subtitle: "SETTINGS$PAGE_ORG_MEMBERS_SUBLINE",
    section: "org",
  },
  {
    icon: <BarChart2 className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/usage-monitoring",
    text: "SETTINGS$NAV_ADMIN_DASHBOARD",
    subtitle: "SETTINGS$PAGE_USAGE_MONITORING_SUBLINE",
    section: "org",
  },
  {
    icon: <DollarSign className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/budgets",
    text: "SETTINGS$NAV_BUDGETS",
    subtitle: "SETTINGS$PAGE_BUDGETS_SUBLINE",
    section: "org",
  },
  {
    icon: <CreditCardIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/credits",
    text: "SETTINGS$NAV_CREDITS",
    subtitle: "SETTINGS$PAGE_CREDITS_SUBLINE",
    section: "org",
  },
  {
    icon: <CircuitIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/org-defaults",
    text: "COMMON$LANGUAGE_MODEL_LLM",
    subtitle: "SETTINGS$PAGE_LLM_SUBLINE",
    section: "org",
  },
  {
    icon: <MemoryIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/org-defaults/condenser",
    text: "SETTINGS$NAV_CONDENSER",
    subtitle: "SETTINGS$PAGE_CONDENSER_SUBLINE",
    section: "org",
  },
  {
    icon: <Shield className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/org-defaults/verification",
    text: "SETTINGS$NAV_VERIFICATION",
    subtitle: "SETTINGS$PAGE_VERIFICATION_SUBLINE",
    section: "org",
  },
  {
    icon: <RobotIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/agent",
    text: "SETTINGS$AGENT",
    subtitle: "SETTINGS$PAGE_AGENT_SUBLINE",
    section: "personal",
  },
  {
    icon: <CircuitIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings",
    text: "COMMON$LANGUAGE_MODEL_LLM",
    subtitle: "SETTINGS$PAGE_LLM_SUBLINE",
    section: "personal",
    disabledByAcp: true,
  },
  {
    icon: <MemoryIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/condenser",
    text: "SETTINGS$NAV_CONDENSER",
    subtitle: "SETTINGS$PAGE_CONDENSER_SUBLINE",
    section: "personal",
    disabledByAcp: true,
  },
  {
    icon: <Shield className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/verification",
    text: "SETTINGS$NAV_VERIFICATION",
    subtitle: "SETTINGS$PAGE_VERIFICATION_SUBLINE",
    section: "personal",
  },
  {
    icon: <KeyIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/api-keys",
    text: "SETTINGS$NAV_API_KEYS",
    subtitle: "SETTINGS$PAGE_API_KEYS_SUBLINE",
    section: "personal",
  },
  {
    icon: <KeyIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/secrets",
    text: "SETTINGS$NAV_SECRETS",
    subtitle: "SETTINGS$PAGE_SECRETS_SUBLINE",
    section: "personal",
  },
  {
    icon: <ServerProcessIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/mcp",
    text: "SETTINGS$NAV_MCP",
    subtitle: "SETTINGS$PAGE_MCP_SUBLINE",
    section: "personal",
  },
  {
    icon: <PuzzlePieceIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/integrations",
    text: "SETTINGS$NAV_INTEGRATIONS",
    subtitle: "SETTINGS$PAGE_INTEGRATIONS_SUBLINE",
    section: "personal",
  },
  {
    icon: (
      <SkillsIcon width={ICON_SIZE} height={ICON_SIZE} aria-hidden="true" />
    ),
    to: "/settings/skills",
    text: "SETTINGS$NAV_SKILLS",
    subtitle: "SETTINGS$PAGE_SKILLS_SUBLINE",
    section: "personal",
  },
  {
    icon: <CreditCardIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/billing",
    text: "SETTINGS$NAV_BILLING",
    subtitle: "SETTINGS$PAGE_BILLING_SUBLINE",
    section: "billing",
  },
  {
    icon: <UserIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/user",
    text: "SETTINGS$NAV_USER",
    subtitle: "SETTINGS$PAGE_USER_SUBLINE",
    section: "user",
  },
  {
    icon: <AppWindow className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/app",
    text: "SETTINGS$NAV_APPLICATION",
    subtitle: "SETTINGS$PAGE_APPLICATION_SUBLINE",
    section: "user",
  },
  {
    icon: <BarChart2 className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/quota",
    text: "SETTINGS$NAV_QUOTA",
    subtitle: "SETTINGS$PAGE_QUOTA_SUBLINE",
    section: "user",
  },
];

export const OSS_NAV_ITEMS: SettingsNavItem[] = [
  {
    icon: <RobotIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/agent",
    text: "SETTINGS$AGENT",
    subtitle: "SETTINGS$PAGE_AGENT_SUBLINE",
  },
  {
    icon: <CircuitIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings",
    text: "SETTINGS$NAV_LLM",
    subtitle: "SETTINGS$PAGE_LLM_SUBLINE",
    disabledByAcp: true,
  },
  {
    icon: <MemoryIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/condenser",
    text: "SETTINGS$NAV_CONDENSER",
    subtitle: "SETTINGS$PAGE_CONDENSER_SUBLINE",
    disabledByAcp: true,
  },
  {
    icon: <Shield className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/verification",
    text: "SETTINGS$NAV_VERIFICATION",
    subtitle: "SETTINGS$PAGE_VERIFICATION_SUBLINE",
  },
  {
    icon: <ServerProcessIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/mcp",
    text: "SETTINGS$NAV_MCP",
    subtitle: "SETTINGS$PAGE_MCP_SUBLINE",
  },
  {
    icon: (
      <SkillsIcon width={ICON_SIZE} height={ICON_SIZE} aria-hidden="true" />
    ),
    to: "/settings/skills",
    text: "SETTINGS$NAV_SKILLS",
    subtitle: "SETTINGS$PAGE_SKILLS_SUBLINE",
  },
  {
    icon: <PuzzlePieceIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/integrations",
    text: "SETTINGS$NAV_INTEGRATIONS",
    subtitle: "SETTINGS$PAGE_INTEGRATIONS_SUBLINE",
  },
  {
    icon: <AppWindow className="size-4" strokeWidth={2} aria-hidden />,
    to: "/settings/app",
    text: "SETTINGS$NAV_APPLICATION",
    subtitle: "SETTINGS$PAGE_APPLICATION_SUBLINE",
  },
  {
    icon: <KeyIcon width={ICON_SIZE} height={ICON_SIZE} />,
    to: "/settings/secrets",
    text: "SETTINGS$NAV_SECRETS",
    subtitle: "SETTINGS$PAGE_SECRETS_SUBLINE",
  },
];
