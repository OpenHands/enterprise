import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import { GettingStartedPage } from "#/components/features/setup/getting-started";
import { SETUP_FINISHED_STORAGE_PREFIX } from "#/utils/org/setup-readiness";

const markFinished = vi.fn();
const markAutomationDone = vi.fn();

vi.mock("#/hooks/query/use-org-setup", () => ({
  useOrgSetup: () => ({
    persona: "admin",
    checklist: {
      remaining: [
        {
          id: "invite",
          kind: "org",
          personas: ["admin"],
          titleKey: "SETUP$STEP_INVITE",
          descriptionKey: "SETUP$STEP_INVITE_HINT",
          actionLabelKey: "SETUP$ACTION_INVITE",
          to: "/settings/org-members",
          required: true,
        },
      ],
      completed: [
        {
          id: "llm",
          kind: "org",
          personas: ["admin"],
          titleKey: "SETUP$STEP_LLM",
          descriptionKey: "SETUP$STEP_LLM_HINT",
          actionLabelKey: "SETUP$ACTION_LLM",
          to: "/settings/org-defaults",
          required: true,
        },
      ],
      requiredTotal: 2,
      requiredDone: 1,
      nextStep: {
        id: "invite",
        kind: "org",
        personas: ["admin"],
        titleKey: "SETUP$STEP_INVITE",
        descriptionKey: "SETUP$STEP_INVITE_HINT",
        actionLabelKey: "SETUP$ACTION_INVITE",
        to: "/settings/org-members",
        required: true,
      },
      isCoreComplete: false,
      isFullyComplete: false,
    },
    progress: 0.5,
    markFinished,
    markAutomationDone,
  }),
}));

describe("GettingStartedPage", () => {
  beforeEach(() => {
    markFinished.mockClear();
    markAutomationDone.mockClear();
    window.localStorage.removeItem(`${SETUP_FINISHED_STORAGE_PREFIX}org-1`);
  });

  it("renders remaining and completed steps", () => {
    const RouterStub = createRoutesStub([
      { path: "/settings/getting-started", Component: GettingStartedPage },
      { path: "/settings/org-members", Component: () => <div /> },
    ]);
    render(<RouterStub initialEntries={["/settings/getting-started"]} />);

    expect(screen.getByTestId("getting-started-page")).toBeInTheDocument();
    expect(screen.getByTestId("setup-step-invite")).toBeInTheDocument();
    expect(screen.getByTestId("setup-step-llm")).toBeInTheDocument();
    expect(screen.getByTestId("setup-finish")).toBeDisabled();
  });

  it("navigates from a step action", async () => {
    const user = userEvent.setup();
    const RouterStub = createRoutesStub([
      { path: "/settings/getting-started", Component: GettingStartedPage },
      {
        path: "/settings/org-members",
        Component: () => <div data-testid="members-stub" />,
      },
    ]);
    render(<RouterStub initialEntries={["/settings/getting-started"]} />);

    await user.click(screen.getByTestId("setup-step-action-invite"));
    expect(screen.getByTestId("members-stub")).toBeInTheDocument();
  });
});
