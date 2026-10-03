import { replace, useLocation, useNavigate, useOutlet } from "react-router";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { AnimatePresence, motion } from "framer-motion";
import OptionService from "#/api/option-service/option-service.api";
import { WebClientConfig } from "#/api/option-service/option.types";
import { InstallFooter } from "#/components/features/super-admin/install-footer";
import { InstallStepBar } from "#/components/features/super-admin/install-step-bar";
import { CONFIG_CACHE_OPTIONS, QUERY_KEYS } from "#/hooks/query/query-keys";
import { queryClient } from "#/query-client-config";
import {
  getSuperAdminNuxPath,
  getSuperAdminNuxStep,
  readSuperAdminNux,
  subscribeSuperAdminNux,
} from "#/utils/org/super-admin-nux";

/** The install wizard ships behind ENABLE_SUPER_ADMIN; leave it when the flag is off. */
export const clientLoader = async () => {
  const config = await queryClient.fetchQuery<WebClientConfig>({
    queryKey: QUERY_KEYS.WEB_CLIENT_CONFIG,
    queryFn: OptionService.getConfig,
    ...CONFIG_CACHE_OPTIONS,
  });

  if (!config?.feature_flags?.enable_super_admin) {
    return replace("/");
  }

  return {};
};

/** How long the outgoing and incoming install steps overlap. */
const INSTALL_CROSSFADE_S = 0.5;

const INSTALL_CROSSFADE_EASE = [0.22, 1, 0.36, 1] as const;

/**
 * Each step mounts its own copy of the outlet and keeps it.
 *
 * `useOutlet()` always reflects the *current* location. Without this freeze,
 * the step that is fading out would re-render as the next screen and the
 * crossfade would dissolve a screen into itself.
 */
function FrozenInstallOutlet() {
  const outlet = useOutlet();
  const [frozen] = useState(outlet);
  return frozen;
}

function prefersReducedMotion() {
  if (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  ) {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * One install step, stacked over the previous step for the crossfade.
 *
 * Pointer events stay off until the step is mostly visible, so a click
 * during the dissolve cannot land on a screen the user cannot see yet.
 * The first step skips that wait — it is already fully visible.
 */
function InstallStepLayer({
  zIndex,
  reduceMotion,
}: {
  zIndex: number;
  reduceMotion: boolean;
}) {
  const [interactive, setInteractive] = useState(reduceMotion || zIndex === 1);

  return (
    <motion.div
      className="absolute inset-0 overflow-y-auto"
      style={{
        zIndex,
        pointerEvents: interactive ? "auto" : "none",
      }}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{
        duration: reduceMotion ? 0 : INSTALL_CROSSFADE_S,
        ease: INSTALL_CROSSFADE_EASE,
      }}
      onUpdate={(latest) => {
        const opacity = typeof latest.opacity === "number" ? latest.opacity : 1;
        const next = opacity > 0.85;
        setInteractive((current) => (current === next ? current : next));
      }}
      data-testid="super-admin-install-crossfade"
    >
      <div className="flex min-h-full flex-col">
        <div className="flex flex-1 items-center justify-center px-6 pb-10 pt-28">
          <FrozenInstallOutlet />
        </div>
        <InstallFooter />
      </div>
    </motion.div>
  );
}

/**
 * Full-bleed blank shell for first-install Super Admin NUX (no settings chrome).
 *
 * Welcome, terms, account, company, and the first organization stay mounted together
 * for one beat so the outgoing step can fade out while the next step fades
 * in over the same background.
 */
export default function SuperAdminInstallLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { pathname } = location;
  const state = useSyncExternalStore(
    subscribeSuperAdminNux,
    readSuperAdminNux,
    readSuperAdminNux,
  );
  const layerRef = useRef(0);
  const zIndexByPath = useRef<Record<string, number>>({});

  if (zIndexByPath.current[pathname] == null) {
    layerRef.current += 1;
    zIndexByPath.current[pathname] = layerRef.current;
  }

  const catalogPreview =
    new URLSearchParams(location.search).get("catalogPreview") === "1";

  useEffect(() => {
    if (catalogPreview) {
      return;
    }
    const step = getSuperAdminNuxStep(state);
    const expected = getSuperAdminNuxPath(step);
    // Keep user on the correct step; allow forward-only paths that match.
    if (step === "done") {
      navigate(expected, { replace: true });
      return;
    }
    if (pathname !== expected && !pathname.startsWith(expected)) {
      // If they're ahead of progress, send them back to the next incomplete step.
      const order = [
        "/install",
        "/install/tos",
        "/install/account",
        "/install/company",
        "/install/org",
      ];
      const currentIdx = order.indexOf(pathname);
      const expectedIdx = order.indexOf(expected);
      if (currentIdx < 0 || currentIdx > expectedIdx) {
        navigate(expected, { replace: true });
      }
    }
  }, [catalogPreview, state, pathname, navigate]);

  const reduceMotion = prefersReducedMotion();

  return (
    <div
      className="relative min-h-screen w-full bg-base"
      data-testid="super-admin-install-shell"
    >
      <AnimatePresence initial={false}>
        <InstallStepLayer
          key={pathname}
          zIndex={zIndexByPath.current[pathname] ?? 1}
          reduceMotion={reduceMotion}
        />
      </AnimatePresence>
      <InstallStepBar pathname={pathname} />
    </div>
  );
}
