import {
  Building2,
  ClipboardList,
  LayoutDashboard,
  ServerCog,
  ShieldCheck,
  Users,
} from "lucide-react";
import { SettingsNavItem } from "#/constants/settings-nav";

export const SUPER_ADMIN_PATHS = {
  root: "/super-admin",
  setup: "/super-admin/setup",
  organizations: "/super-admin/organizations",
  users: "/super-admin/users",
  admins: "/super-admin/admins",
  instance: "/super-admin/instance",
} as const;

export const SUPER_ADMIN_SETUP_ITEM: SettingsNavItem = {
  icon: <ClipboardList className="size-4" strokeWidth={2} aria-hidden />,
  to: SUPER_ADMIN_PATHS.setup,
  text: "SUPER_ADMIN$SETUP_GUIDE",
  subtitle: "SUPER_ADMIN$SETUP_SUBLINE",
};

/** Account-menu jump into the Super Admin app. Not a Settings nav item. */
export const SUPER_ADMIN_ENTRY_ITEM: SettingsNavItem = {
  icon: <ShieldCheck className="size-4" strokeWidth={2} aria-hidden />,
  to: SUPER_ADMIN_PATHS.root,
  text: "SUPER_ADMIN$TITLE",
  subtitle: "SUPER_ADMIN$OVERVIEW_SUBLINE",
};

export const SUPER_ADMIN_NAV_ITEMS: SettingsNavItem[] = [
  {
    icon: <LayoutDashboard className="size-4" strokeWidth={2} aria-hidden />,
    to: SUPER_ADMIN_PATHS.root,
    text: "SUPER_ADMIN$NAV_OVERVIEW",
    subtitle: "SUPER_ADMIN$OVERVIEW_SUBLINE",
  },
  {
    icon: <Building2 className="size-4" strokeWidth={2} aria-hidden />,
    to: SUPER_ADMIN_PATHS.organizations,
    text: "SUPER_ADMIN$NAV_ORGANIZATIONS",
    subtitle: "SUPER_ADMIN$ORGANIZATIONS_SUBLINE",
  },
  {
    icon: <Users className="size-4" strokeWidth={2} aria-hidden />,
    to: SUPER_ADMIN_PATHS.users,
    text: "SUPER_ADMIN$NAV_USERS",
    subtitle: "SUPER_ADMIN$USERS_SUBLINE",
  },
  {
    icon: <ShieldCheck className="size-4" strokeWidth={2} aria-hidden />,
    to: SUPER_ADMIN_PATHS.admins,
    text: "SUPER_ADMIN$NAV_ADMINS",
    subtitle: "SUPER_ADMIN$ADMINS_SUBLINE",
  },
  {
    icon: <ServerCog className="size-4" strokeWidth={2} aria-hidden />,
    to: SUPER_ADMIN_PATHS.instance,
    text: "SUPER_ADMIN$NAV_INSTANCE",
    subtitle: "SUPER_ADMIN$INSTANCE_SUBLINE",
  },
];
