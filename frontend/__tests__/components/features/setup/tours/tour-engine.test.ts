import { waitFor } from "@testing-library/react";
import type { Config } from "driver.js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isGuidedTourActive,
  startGuidedTour,
  stopGuidedTour,
} from "#/components/features/setup/tours/tour-engine";
import {
  SUPER_ADMIN_SETUP_STEP_EVENT,
  type GuidedTour,
} from "#/components/features/setup/tours/types";

const { drivers } = vi.hoisted(() => ({
  drivers: [] as { destroy: ReturnType<typeof vi.fn> }[],
}));

// Like driver.js, destroying a tour runs its onDestroyed hook.
vi.mock("driver.js", () => ({
  driver: (config: Config) => {
    const instance = {
      drive: vi.fn(),
      refresh: vi.fn(),
      moveTo: vi.fn(),
      moveNext: vi.fn(),
      movePrevious: vi.fn(),
      destroy: vi.fn(() => config.onDestroyed?.(undefined, {}, {} as never)),
    };
    drivers.push(instance);
    return instance;
  },
}));

const SAVE_TOUR: GuidedTour = {
  id: "save",
  title: "Save",
  steps: [
    {
      id: "save",
      route: "/",
      anchor: "#anchor",
      title: "Save",
      body: "Save the form.",
      waitForComplete: "add-llm",
    },
  ],
};

const NEXT_TOUR: GuidedTour = {
  id: "next",
  title: "Next",
  steps: [
    {
      id: "next",
      route: "/",
      anchor: "#anchor",
      title: "Next",
      body: "The next step.",
    },
  ],
};

const wait = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

function reportStepDone(id: string) {
  window.dispatchEvent(
    new CustomEvent(SUPER_ADMIN_SETUP_STEP_EVENT, { detail: { id } }),
  );
}

describe("startGuidedTour", () => {
  beforeEach(() => {
    document.body.innerHTML = '<button id="anchor" type="button">Save</button>';
    Element.prototype.scrollIntoView = vi.fn();
    drivers.length = 0;
  });

  afterEach(() => {
    stopGuidedTour();
    document.body.innerHTML = "";
  });

  it("ends the tour when the action its last stop waits for succeeds", async () => {
    // Arrange
    await startGuidedTour(SAVE_TOUR, vi.fn());
    await wait(60); // the stop starts waiting once it is shown

    // Act
    reportStepDone("add-llm");

    // Assert
    await waitFor(() => expect(isGuidedTourActive()).toBe(false));
    expect(drivers[0].destroy).toHaveBeenCalledTimes(1);
  });

  it("does not let a replaced tour act on the tour that replaced it", async () => {
    // Arrange: the first tour's stop is done, so it is about to end itself.
    await startGuidedTour(SAVE_TOUR, vi.fn());
    await wait(60);
    reportStepDone("add-llm");

    // Act: the next step's tour starts before that happens.
    await startGuidedTour(NEXT_TOUR, vi.fn());
    await wait(150);

    // Assert
    expect(isGuidedTourActive()).toBe(true);
    expect(drivers[0].destroy).toHaveBeenCalledTimes(1);
    expect(drivers[1].destroy).not.toHaveBeenCalled();
  });
});
