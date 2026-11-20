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
  drivers: [] as {
    drive: ReturnType<typeof vi.fn>;
    destroy: ReturnType<typeof vi.fn>;
    moveTo: ReturnType<typeof vi.fn>;
  }[],
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

// Like the Add LLM profile flow: a button on the page, then a form stop that
// waits for the profile to be saved.
const FORM_TOUR: GuidedTour = {
  id: "form",
  title: "Form",
  steps: [
    {
      id: "open",
      route: "/",
      anchor: "#anchor",
      title: "Open",
      body: "Open the form.",
      nextClicksAnchor: true,
    },
    {
      id: "form",
      route: "/",
      anchor: "#form",
      title: "Form",
      body: "Fill in and save the form.",
      waitForComplete: "add-llm",
    },
  ],
};

const wait = (ms: number) =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

// Like ModalBackdrop: a modal dialog on top of the page.
function openDialog(content = "") {
  document.body.insertAdjacentHTML(
    "beforeend",
    `<div role="dialog" aria-modal="true">${content}</div>`,
  );
}

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

  it("starts on the stop in a dialog the admin has already opened", async () => {
    // Arrange: the click the first stop waits for came before the tour.
    openDialog('<div id="form"></div>');

    // Act
    await startGuidedTour(FORM_TOUR, vi.fn());

    // Assert
    expect(drivers[0].drive).toHaveBeenCalledWith(1);
  });

  it("does not start while a dialog with none of its stops covers the page", async () => {
    // Arrange
    openDialog();

    // Act
    await startGuidedTour(FORM_TOUR, vi.fn());

    // Assert: the first stop would be drawn over that dialog.
    expect(drivers).toHaveLength(0);
    expect(isGuidedTourActive()).toBe(false);
  });

  it("starts on the form stop when the admin has already opened the form", async () => {
    // Arrange: the form has replaced the button the first stop points at.
    document.body.innerHTML = '<div id="form"></div>';

    // Act
    await startGuidedTour(FORM_TOUR, vi.fn());

    // Assert
    expect(drivers[0].drive).toHaveBeenCalledWith(1);
  });

  it("ends the tour when the element its waiting stop is on leaves the page", async () => {
    // Arrange
    await startGuidedTour(SAVE_TOUR, vi.fn());
    await wait(60);

    // Act: the admin closes the dialog or goes back to the list.
    document.getElementById("anchor")!.remove();

    // Assert
    await waitFor(() => expect(isGuidedTourActive()).toBe(false));
    expect(drivers[0].destroy).toHaveBeenCalledTimes(1);
  });

  it("still shows the next stop when the waiting stop's element leaves after its step succeeds", async () => {
    // Arrange
    const tour: GuidedTour = {
      ...NEXT_TOUR,
      steps: [SAVE_TOUR.steps[0], { ...NEXT_TOUR.steps[0], anchor: "#next" }],
    };
    document.body.insertAdjacentHTML("beforeend", '<div id="next"></div>');
    await startGuidedTour(tour, vi.fn());
    await wait(60);

    // Act: the saved form closes as the tour moves on.
    reportStepDone("add-llm");
    document.getElementById("anchor")!.remove();

    // Assert
    await waitFor(() => expect(drivers[0].moveTo).toHaveBeenCalledWith(1));
    expect(isGuidedTourActive()).toBe(true);
  });
});
