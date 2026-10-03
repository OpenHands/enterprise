import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";
import type { GuidedTour, GuidedTourSide, GuidedTourStep } from "./types";
import {
  GUIDED_TOUR_ALSO_HIGHLIGHT_CLASS,
  SUPER_ADMIN_SETUP_STEP_EVENT,
} from "./types";
import "./tour-theme.css";

const DRAWER_MS = 320;

let activeDriver: Driver | null = null;
const resolvedAnchors = new Map<string, Element>();
const tourActiveListeners = new Set<() => void>();
let tourActive = false;
let setupStepListener: ((event: Event) => void) | null = null;

function setTourActive(next: boolean): void {
  if (tourActive === next) {
    return;
  }
  tourActive = next;
  tourActiveListeners.forEach((listener) => listener());
}

export function isGuidedTourActive(): boolean {
  return tourActive;
}

export function subscribeGuidedTourActive(listener: () => void): () => void {
  tourActiveListeners.add(listener);
  return () => {
    tourActiveListeners.delete(listener);
  };
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function classifyAnchor(el: Element): "desktop-nav" | "mobile-nav" | "page" {
  if (
    el.closest('[data-testid="settings-navbar-desktop"]') ||
    el.closest('[data-testid="super-admin-navbar"]')
  ) {
    return "desktop-nav";
  }
  if (
    el.closest('[data-testid="settings-navbar"]') ||
    el.closest('[data-testid="super-admin-navbar-mobile"]')
  ) {
    return "mobile-nav";
  }
  return "page";
}

function isDesktopRailVisible(): boolean {
  const rail =
    document.querySelector('[data-testid="settings-navbar-desktop"]') ||
    document.querySelector('[data-testid="super-admin-navbar"]');
  return Boolean(rail && window.getComputedStyle(rail).display !== "none");
}

async function closeMobileDrawer(): Promise<void> {
  const close = document.querySelector<HTMLButtonElement>(
    'button[aria-label="Close navigation menu"], button[aria-label="Toggle Super Admin menu"]',
  );
  const drawer =
    document.querySelector('[data-testid="settings-navbar"]') ||
    document.querySelector('[data-testid="super-admin-navbar-mobile"]');
  if (!close || !drawer) {
    return;
  }
  const hidden =
    window.getComputedStyle(drawer).display === "none" ||
    drawer.className.includes("-translate-x-full");
  if (hidden) {
    return;
  }
  close.click();
  await wait(DRAWER_MS);
}

async function openMobileDrawer(): Promise<void> {
  const drawer =
    document.querySelector('[data-testid="settings-navbar"]') ||
    document.querySelector('[data-testid="super-admin-navbar-mobile"]');
  if (drawer && !drawer.className.includes("-translate-x-full")) {
    return;
  }
  const toggle = document.querySelector<HTMLButtonElement>(
    'button[aria-label="Toggle settings menu"], button[aria-label="Toggle Super Admin menu"]',
  );
  if (!toggle || window.getComputedStyle(toggle).display === "none") {
    return;
  }
  toggle.click();
  await wait(DRAWER_MS);
}

function queryFirst(selector: string): Element | null {
  const parts = selector.split(",").map((part) => part.trim());
  for (const part of parts) {
    const el = document.querySelector(part);
    if (el) {
      return el;
    }
  }
  return null;
}

async function revealAnchor(selector: string): Promise<Element | null> {
  const candidates = selector
    .split(",")
    .map((part) => part.trim())
    .flatMap((part) => [...document.querySelectorAll(part)]);
  const pageEl = candidates.find((el) => classifyAnchor(el) === "page");
  const desktopEl = candidates.find(
    (el) => classifyAnchor(el) === "desktop-nav",
  );
  const mobileEl = candidates.find((el) => classifyAnchor(el) === "mobile-nav");

  if (pageEl) {
    await closeMobileDrawer();
    pageEl.scrollIntoView({ block: "nearest", inline: "nearest" });
    return pageEl;
  }
  if (isDesktopRailVisible() && desktopEl) {
    await closeMobileDrawer();
    desktopEl.scrollIntoView({ block: "nearest", inline: "nearest" });
    return desktopEl;
  }
  if (mobileEl) {
    await openMobileDrawer();
    mobileEl.scrollIntoView({ block: "nearest", inline: "nearest" });
    return mobileEl;
  }
  return queryFirst(selector);
}

function sideFor(el: Element, requested?: GuidedTourSide): GuidedTourSide {
  const rect = el.getBoundingClientRect();
  const spaceRight = window.innerWidth - rect.right;
  const spaceBottom = window.innerHeight - rect.bottom;
  if (requested === "right" && spaceRight > 340) {
    return "right";
  }
  if (requested === "left" && rect.left > 340) {
    return "left";
  }
  if (spaceBottom > 220) {
    return "bottom";
  }
  if (rect.top > 220) {
    return "top";
  }
  return requested ?? "bottom";
}

async function resolveAnchorWithRetries(
  selector: string,
): Promise<Element | null> {
  let el: Element | null = null;
  let attempt = 0;
  /* eslint-disable no-await-in-loop -- intentional short retries while route mounts */
  while (attempt < 20 && !el) {
    el = await revealAnchor(selector);
    if (!el) {
      await wait(100);
    }
    attempt += 1;
  }
  /* eslint-enable no-await-in-loop */
  return el;
}

let alsoHighlightTarget: Element | null = null;
let alsoHighlightStage: HTMLElement | null = null;

function syncAlsoHighlightStage(): void {
  if (!alsoHighlightStage || !alsoHighlightTarget) {
    return;
  }
  const rect = alsoHighlightTarget.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) {
    alsoHighlightStage.style.visibility = "hidden";
    return;
  }
  const pad = 8;
  alsoHighlightStage.style.visibility = "visible";
  alsoHighlightStage.style.top = `${Math.round(rect.top - pad)}px`;
  alsoHighlightStage.style.left = `${Math.round(rect.left - pad)}px`;
  alsoHighlightStage.style.width = `${Math.round(rect.width + pad * 2)}px`;
  alsoHighlightStage.style.height = `${Math.round(rect.height + pad * 2)}px`;
}

function clearAlsoHighlight(): void {
  window.removeEventListener("resize", syncAlsoHighlightStage);
  window.removeEventListener("scroll", syncAlsoHighlightStage, true);
  alsoHighlightStage?.remove();
  alsoHighlightStage = null;
  alsoHighlightTarget = null;
  document
    .querySelectorAll(`.${GUIDED_TOUR_ALSO_HIGHLIGHT_CLASS}`)
    .forEach((el) => {
      el.classList.remove(GUIDED_TOUR_ALSO_HIGHLIGHT_CLASS);
    });
}

function queryVisible(selector: string): Element | null {
  const parts = selector.split(",").map((part) => part.trim());
  for (const part of parts) {
    for (const el of document.querySelectorAll(part)) {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        return el;
      }
    }
  }
  return null;
}

/**
 * Left-nav tabs sit under a stacking context below the driver SVG overlay,
 * so a CSS class on the tab itself never shows. Mirror a fixed stage on
 * document.body (above the overlay) that clones the tab appearance.
 */
function applyAlsoHighlight(step: GuidedTourStep): void {
  clearAlsoHighlight();
  if (!step.alsoHighlight) {
    return;
  }
  const el = queryVisible(step.alsoHighlight);
  if (!(el instanceof HTMLElement)) {
    return;
  }

  alsoHighlightTarget = el;
  el.classList.add(GUIDED_TOUR_ALSO_HIGHLIGHT_CLASS);
  el.scrollIntoView({ block: "nearest", inline: "nearest" });

  const stage = document.createElement("div");
  stage.className = "oh-guided-tour-also-stage";
  stage.setAttribute("aria-hidden", "true");

  const clone = el.cloneNode(true) as HTMLElement;
  clone.classList.add("oh-guided-tour-also-stage-inner");
  clone.removeAttribute("data-testid");
  clone.removeAttribute("href");
  clone.setAttribute("tabindex", "-1");
  stage.appendChild(clone);

  document.body.appendChild(stage);
  alsoHighlightStage = stage;
  syncAlsoHighlightStage();

  window.addEventListener("resize", syncAlsoHighlightStage);
  window.addEventListener("scroll", syncAlsoHighlightStage, true);
}

async function prepareStep(
  step: GuidedTourStep,
  navigate: (to: string) => void,
): Promise<Element | null> {
  if (window.location.pathname !== step.route) {
    navigate(step.route);
    // Org switch + settings loader need a beat before anchors exist.
    await wait(400);
  }

  let el = await resolveAnchorWithRetries(step.anchor);
  if (!el && step.openViaClick) {
    const opener = queryFirst(step.openViaClick);
    if (opener instanceof HTMLElement) {
      opener.click();
      await wait(280);
      el = await resolveAnchorWithRetries(step.anchor);
    }
  }

  if (el) {
    resolvedAnchors.set(step.id, el);
  } else {
    resolvedAnchors.delete(step.id);
  }
  applyAlsoHighlight(step);
  return el;
}

function clearSetupStepListener(): void {
  if (setupStepListener) {
    window.removeEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, setupStepListener);
    setupStepListener = null;
  }
}

function findStartIndex(tour: GuidedTour, startAtStepId?: string): number {
  if (!startAtStepId) {
    return 0;
  }
  const exact = tour.steps.findIndex((step) => step.id === startAtStepId);
  if (exact >= 0) {
    return exact;
  }
  const byChecklist = tour.steps.findIndex(
    (step) => step.checklistId === startAtStepId,
  );
  return Math.max(0, byChecklist);
}

export function stopGuidedTour(): void {
  clearSetupStepListener();
  clearAlsoHighlight();
  activeDriver?.destroy();
  activeDriver = null;
  resolvedAnchors.clear();
  setTourActive(false);
}

export async function startGuidedTour(
  tour: GuidedTour,
  navigate: (to: string) => void,
  options?: {
    startAtStepId?: string;
    onStep?: (stepId: string, index: number) => void;
    onComplete?: () => void;
    onDismiss?: (stepIndex: number) => void;
  },
): Promise<void> {
  stopGuidedTour();

  const startIndex = findStartIndex(tour, options?.startAtStepId);
  const steps = startIndex > 0 ? tour.steps.slice(startIndex) : [...tour.steps];
  if (steps.length === 0) {
    return;
  }

  if (steps[0].route !== window.location.pathname) {
    navigate(steps[0].route);
    await wait(280);
  }
  await prepareStep(steps[0], navigate);

  let index = 0;
  options?.onStep?.(steps[0].id, startIndex);

  let anchorClickHandler: ((event: Event) => void) | null = null;

  const tourCtl: {
    clearAnchorClickHandler: () => void;
    applyWaitingUi: (nextButton: HTMLElement) => void;
    advanceFrom: (instanceApi: Driver, nextIndex: number) => Promise<void>;
    bindWaitForComplete: (instanceApi: Driver) => void;
    bindInteractiveStep: (instanceApi: Driver) => void;
  } = {
    clearAnchorClickHandler: () => {
      if (!anchorClickHandler) {
        return;
      }
      for (const el of resolvedAnchors.values()) {
        el.removeEventListener("click", anchorClickHandler);
      }
      anchorClickHandler = null;
    },
    applyWaitingUi: (nextButton: HTMLElement) => {
      const current = steps[index];
      // driver.js owns this button node; toggle visibility for wait-for-complete stops
      // eslint-disable-next-line no-param-reassign -- tour UI control
      nextButton.style.display = current?.waitForComplete ? "none" : "";
    },
    advanceFrom: async (instanceApi, nextIndex) => {
      const nextStep = steps[nextIndex];
      if (!nextStep) {
        instanceApi.destroy();
        return;
      }
      clearSetupStepListener();
      tourCtl.clearAnchorClickHandler();
      await prepareStep(nextStep, navigate);
      index = nextIndex;
      options?.onStep?.(nextStep.id, startIndex + index);
      // Prefer moveTo so we can jump past micro-steps (e.g. create-org →
      // add-llm when completion fires before the form stop was reached).
      if (typeof instanceApi.moveTo === "function") {
        instanceApi.moveTo(nextIndex);
      } else {
        instanceApi.moveNext();
      }
      window.setTimeout(() => {
        instanceApi.refresh();
        tourCtl.bindInteractiveStep(instanceApi);
      }, 30);
    },
    bindWaitForComplete: (instanceApi) => {
      clearSetupStepListener();
      // Listen for any waitForComplete id in this tour slice. Completion may
      // fire while we are still on the preceding click stop (Create button)
      // if the modal was already open, so jump to the step after the waiter.
      const waitedIds = new Set(
        steps
          .map((step) => step.waitForComplete)
          .filter((id): id is string => Boolean(id)),
      );
      if (waitedIds.size === 0) {
        return;
      }
      setupStepListener = (event: Event) => {
        const { detail } = event as CustomEvent<{ id?: string }>;
        const completedId = detail?.id;
        if (!completedId || !waitedIds.has(completedId)) {
          return;
        }
        const waitIndex = steps.findIndex(
          (step) => step.waitForComplete === completedId,
        );
        if (waitIndex < 0 || index > waitIndex) {
          return;
        }
        clearSetupStepListener();
        // Let the completing UI (modal) unmount before we navigate/highlight.
        wait(80)
          .then(() => tourCtl.advanceFrom(instanceApi, waitIndex + 1))
          .catch(() => undefined);
      };
      window.addEventListener(SUPER_ADMIN_SETUP_STEP_EVENT, setupStepListener);
    },
    bindInteractiveStep: (instanceApi) => {
      tourCtl.clearAnchorClickHandler();
      tourCtl.bindWaitForComplete(instanceApi);
      const current = steps[index];
      if (!current?.nextClicksAnchor) {
        return;
      }
      const anchor = resolvedAnchors.get(current.id);
      if (!anchor) {
        return;
      }
      anchorClickHandler = () => {
        tourCtl.clearAnchorClickHandler();
        wait(280)
          .then(() => tourCtl.advanceFrom(instanceApi, index + 1))
          .catch(() => undefined);
      };
      anchor.addEventListener("click", anchorClickHandler);
    },
  };

  const instance = driver({
    showProgress: true,
    allowClose: true,
    // Allow clicking the highlighted control (Create button, form fields).
    disableActiveInteraction: false,
    overlayColor: "#000",
    overlayOpacity: 0.6,
    stagePadding: 8,
    popoverOffset: 16,
    smoothScroll: true,
    popoverClass: "oh-guided-tour-popover",
    steps: steps.map((step) => ({
      element: () => resolvedAnchors.get(step.id) as Element,
      popover: {
        title: step.title,
        description: step.body,
        side: step.side ?? "bottom",
        align: "start",
      },
    })),
    onHighlightStarted: (el, step) => {
      if (el) {
        const { popover } = step;
        if (popover) {
          popover.side = sideFor(
            el,
            popover.side as GuidedTourSide | undefined,
          );
        }
        el.scrollIntoView({ block: "nearest", inline: "nearest" });
      }
      const current = steps[index];
      if (current) {
        applyAlsoHighlight(current);
      }
    },
    onPopoverRender: (popover) => {
      popover.nextButton.classList.add("oh-guided-tour-next");
      popover.previousButton.classList.add("oh-guided-tour-prev");
      tourCtl.applyWaitingUi(popover.nextButton);
    },
    onNextClick: async (_el, _step, { driver: instanceApi }) => {
      const current = steps[index];
      if (current?.waitForComplete) {
        // Completion is event-driven; Next is hidden.
        return;
      }
      tourCtl.clearAnchorClickHandler();
      if (current?.nextClicksAnchor) {
        const anchor = resolvedAnchors.get(current.id);
        if (anchor instanceof HTMLElement) {
          anchor.click();
          await wait(280);
        }
      }
      await tourCtl.advanceFrom(instanceApi, index + 1);
    },
    onPrevClick: async (_el, _step, { driver: instanceApi }) => {
      const prevStep = steps[index - 1];
      if (!prevStep) {
        return;
      }
      clearSetupStepListener();
      tourCtl.clearAnchorClickHandler();
      await prepareStep(prevStep, navigate);
      index = Math.max(0, index - 1);
      options?.onStep?.(prevStep.id, startIndex + index);
      instanceApi.movePrevious();
      window.setTimeout(() => {
        instanceApi.refresh();
        tourCtl.bindInteractiveStep(instanceApi);
      }, 30);
    },
    onCloseClick: (_el, _step, { driver: instanceApi }) => {
      instanceApi.destroy();
    },
    onDestroyed: () => {
      clearSetupStepListener();
      tourCtl.clearAnchorClickHandler();
      clearAlsoHighlight();
      const finished = index >= steps.length - 1;
      if (finished) {
        options?.onComplete?.();
      } else {
        options?.onDismiss?.(startIndex + index);
      }
      activeDriver = null;
      resolvedAnchors.clear();
      setTourActive(false);
    },
  });

  activeDriver = instance;
  setTourActive(true);
  applyAlsoHighlight(steps[0]);
  instance.drive();
  window.setTimeout(() => {
    instance.refresh();
    tourCtl.bindInteractiveStep(instance);
    applyAlsoHighlight(steps[index]);
  }, 40);
}
