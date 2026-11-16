import { describe, it, expect, beforeEach } from "vitest";
import { useInitialQueryStore } from "../src/stores/initial-query-store";

describe("Initial Query Behavior", () => {
  beforeEach(() => {
    useInitialQueryStore.getState().reset();
  });

  it("should clear initial query when clearInitialPrompt is called", () => {
    const { setInitialPrompt, clearInitialPrompt, initialPrompt } =
      useInitialQueryStore.getState();

    setInitialPrompt("test query");
    expect(useInitialQueryStore.getState().initialPrompt).toBe("test query");

    clearInitialPrompt();

    expect(useInitialQueryStore.getState().initialPrompt).toBeNull();
  });
});
