import { render, screen } from "@testing-library/react";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, describe, expect, it } from "vitest";
import { AgentCanvasBanner } from "#/components/features/home/home-header/agent-canvas-banner";

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    ns: ["translation"],
    defaultNS: "translation",
    resources: {
      en: {
        translation: {
          HOME$AGENT_CANVAS_BANNER:
            "New User Experience! Please visit <canvasLink>app.all-hands.dev/canvas</canvasLink> to try out OpenHands Cloud with Agent Canvas.",
        },
      },
    },
    interpolation: {
      escapeValue: false,
    },
  });
});

describe("AgentCanvasBanner", () => {
  it("renders the Agent Canvas URL as an underlined link", () => {
    render(
      <I18nextProvider i18n={i18n}>
        <AgentCanvasBanner />
      </I18nextProvider>,
    );

    const link = screen.getByRole("link", {
      name: "app.all-hands.dev/canvas",
    });

    expect(link).toHaveAttribute("href", "https://app.all-hands.dev/canvas");
    expect(link).toHaveClass(
      "border-b-2",
      "border-white",
      "underline",
      "decoration-2",
    );
  });
});
