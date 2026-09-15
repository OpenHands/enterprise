import { createElement, useState, type ReactNode } from "react";
import { BookOpen, Bot, Plug } from "lucide-react";
import { FaMicrosoft } from "react-icons/fa6";
import { PiMicrosoftOutlookLogo, PiMicrosoftTeamsLogo } from "react-icons/pi";
import {
  SiAirtable,
  SiAsana,
  SiAtlassian,
  SiBitbucket,
  SiBox,
  SiBrave,
  SiCanva,
  SiClickhouse,
  SiCloudflare,
  SiConfluence,
  SiDatadog,
  SiDiscord,
  SiElevenlabs,
  SiFigma,
  SiGit,
  SiGithub,
  SiGmail,
  SiGooglecalendar,
  SiGoogledocs,
  SiGoogledrive,
  SiGooglesheets,
  SiHubspot,
  SiHuggingface,
  SiIntercom,
  SiJira,
  SiKagi,
  SiLinear,
  SiMiro,
  SiMongodb,
  SiNetlify,
  SiNotion,
  SiObsidian,
  SiOkta,
  SiPaypal,
  SiPosthog,
  SiQuickbooks,
  SiRedis,
  SiResend,
  SiSalesforce,
  SiSentry,
  SiSlack,
  SiStripe,
  SiSupabase,
  SiTrello,
  SiVercel,
  SiWebflow,
  SiXero,
  SiZoom,
} from "react-icons/si";
import { TbBrandMonday, TbBrandOnedrive } from "react-icons/tb";
import { CUSTOM_HUB_CATALOG_LOGOS } from "./hub-custom-catalog-logos";

const ICON_CLASS = "size-full max-h-5 max-w-5";

function icon(
  Icon: React.ComponentType<{
    className?: string;
    color?: string;
    "aria-hidden"?: boolean;
  }>,
): ReactNode {
  return createElement(Icon, {
    className: ICON_CLASS,
    color: "currentColor",
    "aria-hidden": true,
  });
}

function lucide(Icon: typeof Plug): ReactNode {
  return createElement(Icon, {
    className: ICON_CLASS,
    strokeWidth: 2.25,
    "aria-hidden": true,
  });
}

const MCP_LOGO = lucide(Plug);

const SIMPLE_ICONS_SLUG: Record<string, string> = {
  "atlassian-rovo": "atlassian",
  "brave-search": "brave",
  "browser-mcp": "modelcontextprotocol",
  "cloudflare-bindings": "cloudflare",
  "cloudflare-browser-rendering": "cloudflare",
  "cloudflare-builds": "cloudflare",
  "cloudflare-docs": "cloudflare",
  "cloudflare-observability": "cloudflare",
  "google-calendar": "googlecalendar",
  "google-docs": "googledocs",
  "google-drive": "googledrive",
  "google-sheets": "googlesheets",
  "microsoft-outlook": "microsoftoutlook",
  "microsoft-teams": "microsoftteams",
  "sequential-thinking": "modelcontextprotocol",
  "superhuman-mail": "superhuman",
};

/** Bundled marks for official Hub catalog slugs. */
export const HUB_CATALOG_LOGOS: Record<string, ReactNode> = {
  airtable: icon(SiAirtable),
  asana: icon(SiAsana),
  atlassian: icon(SiAtlassian),
  "atlassian-rovo": icon(SiAtlassian),
  bitbucket: icon(SiBitbucket),
  box: icon(SiBox),
  "brave-search": icon(SiBrave),
  "browser-mcp": CUSTOM_HUB_CATALOG_LOGOS["browser-mcp"],
  canva: icon(SiCanva),
  clickhouse: icon(SiClickhouse),
  "cloudflare-bindings": icon(SiCloudflare),
  "cloudflare-browser-rendering": icon(SiCloudflare),
  "cloudflare-builds": icon(SiCloudflare),
  "cloudflare-docs": icon(SiCloudflare),
  "cloudflare-observability": icon(SiCloudflare),
  confluence: icon(SiConfluence),
  datadog: icon(SiDatadog),
  deepwiki: lucide(BookOpen),
  discord: icon(SiDiscord),
  elevenlabs: icon(SiElevenlabs),
  everything: MCP_LOGO,
  fetch: MCP_LOGO,
  figma: icon(SiFigma),
  filesystem: MCP_LOGO,
  git: icon(SiGit),
  github: icon(SiGithub),
  gmail: icon(SiGmail),
  "google-calendar": icon(SiGooglecalendar),
  "google-docs": icon(SiGoogledocs),
  "google-drive": icon(SiGoogledrive),
  "google-sheets": icon(SiGooglesheets),
  hubspot: icon(SiHubspot),
  huggingface: icon(SiHuggingface),
  intercom: icon(SiIntercom),
  jira: icon(SiJira),
  kagi: icon(SiKagi),
  linear: icon(SiLinear),
  memory: MCP_LOGO,
  "microsoft-outlook": icon(PiMicrosoftOutlookLogo),
  "microsoft-teams": icon(PiMicrosoftTeamsLogo),
  miro: icon(SiMiro),
  monday: icon(TbBrandMonday),
  mongodb: icon(SiMongodb),
  netlify: icon(SiNetlify),
  notion: icon(SiNotion),
  obsidian: icon(SiObsidian),
  okta: icon(SiOkta),
  onedrive: icon(TbBrandOnedrive),
  paypal: icon(SiPaypal),
  posthog: icon(SiPosthog),
  quickbooks: icon(SiQuickbooks),
  redis: icon(SiRedis),
  resend: icon(SiResend),
  salesforce: icon(SiSalesforce),
  sentry: icon(SiSentry),
  "sequential-thinking": MCP_LOGO,
  sharepoint: icon(FaMicrosoft),
  slack: icon(SiSlack),
  stripe: icon(SiStripe),
  supabase: icon(SiSupabase),
  time: MCP_LOGO,
  trello: icon(SiTrello),
  vercel: icon(SiVercel),
  webflow: icon(SiWebflow),
  xero: icon(SiXero),
  zoom: icon(SiZoom),
  ...CUSTOM_HUB_CATALOG_LOGOS,
};

function remoteLogoSrc(slug: string, logoUrl?: string): string {
  if (logoUrl) {
    return logoUrl;
  }
  const simpleSlug = SIMPLE_ICONS_SLUG[slug] ?? slug.replace(/-/g, "");
  return `https://cdn.simpleicons.org/${simpleSlug}/FFFFFF`;
}

function CatalogRemoteLogo({
  slug,
  logoUrl,
  fallback,
}: {
  slug: string;
  logoUrl?: string;
  fallback: ReactNode;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return fallback;
  }
  return (
    <img
      src={remoteLogoSrc(slug, logoUrl)}
      alt=""
      className="size-full object-contain"
      onError={() => setFailed(true)}
    />
  );
}

export function HubCatalogGlyph({
  slug,
  logoUrl,
  fallback,
}: {
  slug: string;
  logoUrl?: string;
  fallback: ReactNode;
}) {
  return (
    HUB_CATALOG_LOGOS[slug] ?? (
      <CatalogRemoteLogo slug={slug} logoUrl={logoUrl} fallback={fallback} />
    )
  );
}

export const HUB_CATALOG_FALLBACK_LOGO = lucide(Bot);
