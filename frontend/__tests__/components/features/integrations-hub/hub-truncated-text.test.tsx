import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HubTruncatedText } from "#/components/features/integrations-hub/hub-truncated-text";

describe("HubTruncatedText", () => {
  it("renders the full text", () => {
    render(<HubTruncatedText text="slack.post_message" />);
    expect(screen.getByText("slack.post_message")).toBeInTheDocument();
  });

  it("uses line-clamp for multi-line overflow", () => {
    render(
      <HubTruncatedText
        text="A long description that may wrap"
        lines={3}
        className="text-xs"
      />,
    );
    expect(screen.getByText("A long description that may wrap").className).toContain(
      "line-clamp-3",
    );
  });
});
