import { useEffect, useState, useSyncExternalStore } from "react";
import { Map } from "lucide-react";
import { useTranslation } from "react-i18next";
import { BrandButton } from "#/components/features/settings/brand-button";
import { useOrgSetup } from "#/hooks/query/use-org-setup";
import { I18nKey } from "#/i18n/declaration";
import {
  PRODUCT_TOUR_DISMISSED_KEY,
  type SetupPersona,
} from "#/utils/org/setup-readiness";
import { cn } from "#/utils/utils";

const TOUR_STOPS: Record<
  SetupPersona,
  { titleKey: I18nKey; bodyKey: I18nKey }[]
> = {
  super_admin: [
    {
      titleKey: I18nKey.PRODUCT_TOUR$SA_STOP_1_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$SA_STOP_1_BODY,
    },
    {
      titleKey: I18nKey.PRODUCT_TOUR$SA_STOP_2_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$SA_STOP_2_BODY,
    },
  ],
  owner: [
    {
      titleKey: I18nKey.PRODUCT_TOUR$OWNER_STOP_1_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$OWNER_STOP_1_BODY,
    },
    {
      titleKey: I18nKey.PRODUCT_TOUR$OWNER_STOP_2_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$OWNER_STOP_2_BODY,
    },
  ],
  admin: [
    {
      titleKey: I18nKey.PRODUCT_TOUR$ADMIN_STOP_1_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$ADMIN_STOP_1_BODY,
    },
    {
      titleKey: I18nKey.PRODUCT_TOUR$ADMIN_STOP_2_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$ADMIN_STOP_2_BODY,
    },
  ],
  member: [
    {
      titleKey: I18nKey.PRODUCT_TOUR$MEMBER_STOP_1_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$MEMBER_STOP_1_BODY,
    },
    {
      titleKey: I18nKey.PRODUCT_TOUR$MEMBER_STOP_2_TITLE,
      bodyKey: I18nKey.PRODUCT_TOUR$MEMBER_STOP_2_BODY,
    },
  ],
};

const tourListeners = new Set<() => void>();

function notifyTour() {
  tourListeners.forEach((l) => l());
}

function readTourDismissed(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage.getItem(PRODUCT_TOUR_DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissProductTour() {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(PRODUCT_TOUR_DISMISSED_KEY, "1");
  notifyTour();
}

function useTourDismissed() {
  return useSyncExternalStore(
    (onStoreChange) => {
      tourListeners.add(onStoreChange);
      const onStorage = (event: StorageEvent) => {
        if (event.key === PRODUCT_TOUR_DISMISSED_KEY) {
          onStoreChange();
        }
      };
      window.addEventListener("storage", onStorage);
      return () => {
        tourListeners.delete(onStoreChange);
        window.removeEventListener("storage", onStorage);
      };
    },
    readTourDismissed,
    () => false,
  );
}

/**
 * Lightweight product-tour entry. Separate from setup checklist.
 * Full spotlight walkthrough can replace the panel content later.
 */
export function ProductTourWidget() {
  const { t } = useTranslation();
  const { checklist, persona, predicates, organizationId } = useOrgSetup();
  const dismissed = useTourDismissed();
  const [open, setOpen] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [autoOffered, setAutoOffered] = useState(false);

  const setupDone =
    predicates.setupFinished ||
    (checklist.isCoreComplete && checklist.remaining.every((s) => !s.required));

  const stops = TOUR_STOPS[persona];
  const stop = stops[Math.min(stepIndex, stops.length - 1)];

  useEffect(() => {
    if (
      !dismissed &&
      setupDone &&
      Boolean(organizationId) &&
      !autoOffered &&
      !open
    ) {
      setOpen(true);
      setAutoOffered(true);
    }
  }, [dismissed, setupDone, organizationId, autoOffered, open]);

  if (dismissed || !organizationId) {
    return null;
  }

  // Setup checklist owns attention until core readiness is done.
  if (!setupDone) {
    return null;
  }

  return (
    <div className="fixed bottom-5 right-5 z-[60] flex flex-col items-end gap-2">
      {open && (
        <div
          data-testid="product-tour-panel"
          className={cn(
            "flex w-[320px] flex-col gap-3 rounded-xl border border-[var(--oh-border)]",
            "bg-base-secondary p-4 shadow-lg",
          )}
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-sm font-semibold text-white">
                {t(I18nKey.PRODUCT_TOUR$TITLE)}
              </p>
              <p className="mt-1 text-xs text-[var(--oh-muted)]">
                {t(I18nKey.PRODUCT_TOUR$SUBTITLE)}
              </p>
            </div>
            <button
              type="button"
              aria-label={t(I18nKey.PRODUCT_TOUR$CLOSE)}
              className="text-[var(--oh-muted)] hover:text-white"
              onClick={() => setOpen(false)}
            >
              ×
            </button>
          </div>
          <div className="rounded-lg bg-base px-3 py-2.5">
            <p className="text-sm font-medium text-white">{t(stop.titleKey)}</p>
            <p className="mt-1 text-xs leading-4 text-[var(--oh-muted)]">
              {t(stop.bodyKey)}
            </p>
          </div>
          <div className="flex items-center justify-between gap-2">
            <BrandButton
              type="button"
              variant="tertiary"
              testId="product-tour-dont-show"
              onClick={() => {
                dismissProductTour();
                setOpen(false);
              }}
            >
              {t(I18nKey.PRODUCT_TOUR$DONT_SHOW)}
            </BrandButton>
            <div className="flex gap-2">
              {stepIndex < stops.length - 1 ? (
                <BrandButton
                  type="button"
                  variant="primary"
                  testId="product-tour-next"
                  onClick={() => setStepIndex((i) => i + 1)}
                >
                  {t(I18nKey.PRODUCT_TOUR$NEXT)}
                </BrandButton>
              ) : (
                <BrandButton
                  type="button"
                  variant="primary"
                  testId="product-tour-done"
                  onClick={() => {
                    dismissProductTour();
                    setOpen(false);
                  }}
                >
                  {t(I18nKey.PRODUCT_TOUR$DONE)}
                </BrandButton>
              )}
            </div>
          </div>
        </div>
      )}
      <button
        type="button"
        data-testid="product-tour-toggle"
        aria-label={t(I18nKey.PRODUCT_TOUR$TITLE)}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-2 rounded-full border border-[var(--oh-border)]",
          "bg-base-secondary px-3.5 py-2 text-sm font-medium text-white",
          "hover:bg-surface-raised",
        )}
      >
        <Map className="size-4" strokeWidth={2} aria-hidden />
        {t(I18nKey.PRODUCT_TOUR$BUTTON)}
      </button>
    </div>
  );
}
