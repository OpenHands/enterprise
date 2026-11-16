export type GuidedTourSide = "top" | "right" | "bottom" | "left";

/** Dispatched when a Super Admin setup checklist step is truly completed. */
export const SUPER_ADMIN_SETUP_STEP_EVENT = "oh-super-admin-setup-step";

/** Class applied to companion nav targets while a tour stop is active. */
export const GUIDED_TOUR_ALSO_HIGHLIGHT_CLASS = "oh-guided-tour-also-highlight";

export interface GuidedTourStep {
  id: string;
  route: string;
  /** CSS selector for the spotlight target */
  anchor: string;
  title: string;
  body: string;
  side?: GuidedTourSide;
  /**
   * When the user presses Next, click the highlighted element first
   * (e.g. open a create modal from its trigger button).
   */
  nextClicksAnchor?: boolean;
  /** If the anchor is missing, click this selector to reveal it. */
  openViaClick?: string;
  /**
   * Stay on this step until SUPER_ADMIN_SETUP_STEP_EVENT fires with this id,
   * then auto-advance. Next is hidden while waiting.
   */
  waitForComplete?: string;
  /** Checklist / setup step this tour stop belongs to (for start-at mapping). */
  checklistId?: string;
  /**
   * Extra selector (e.g. left-nav tab) highlighted alongside the main anchor
   * so users see where the step lives — not a separate tour stop.
   */
  alsoHighlight?: string;
}

export interface GuidedTour {
  id: string;
  title: string;
  steps: GuidedTourStep[];
}
