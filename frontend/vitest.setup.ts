import { afterAll, afterEach, beforeAll, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import { server } from "#/mocks/node";
import "@testing-library/jest-dom/vitest";

HTMLCanvasElement.prototype.getContext = vi.fn();
HTMLElement.prototype.scrollTo = vi.fn();
window.scrollTo = vi.fn();

// Mock ResizeObserver for test environment
class MockResizeObserver {
  observe = vi.fn();

  unobserve = vi.fn();

  disconnect = vi.fn();
}

// jsdom does not implement Screen.orientation, but react-aria reads
// window.screen.orientation.angle on focus and throws when it is missing.
if (!window.screen.orientation) {
  Object.defineProperty(window.screen, "orientation", {
    configurable: true,
    value: {
      angle: 0,
      type: "landscape-primary",
      lock: vi.fn(),
      unlock: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    },
  });
}

// Some jsdom worker contexts lack ProgressEvent, which msw's XHR interceptor
// references when dispatching progress events (causing unhandled rejections).
if (typeof globalThis.ProgressEvent === "undefined") {
  class ProgressEventPolyfill extends Event {
    lengthComputable = false;

    loaded = 0;

    total = 0;
  }

  globalThis.ProgressEvent =
    ProgressEventPolyfill as unknown as typeof ProgressEvent;
  window.ProgressEvent =
    ProgressEventPolyfill as unknown as typeof ProgressEvent;
}

// Mock the i18n provider
vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-i18next")>()),
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: {
      language: "en",
      exists: () => false,
    },
  }),
}));

vi.mock("#/hooks/use-is-on-tos-page", () => ({
  useIsOnTosPage: () => false,
}));

vi.mock("#/hooks/use-is-on-intermediate-page", () => ({
  useIsOnIntermediatePage: () => false,
}));

// Mock useRevalidator from react-router to allow direct store manipulation
// in tests instead of mocking useSelectedOrganizationId hook
vi.mock("react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router")>()),
  useRevalidator: () => ({
    revalidate: vi.fn(),
  }),
}));

// Import the Zustand mock to enable automatic store resets
vi.mock("zustand");

// Mock requests during tests
beforeAll(() => {
  server.listen({ onUnhandledRequest: "bypass" });
  vi.stubGlobal("ResizeObserver", MockResizeObserver);
});
afterEach(() => {
  server.resetHandlers();
  // Cleanup the document body after each test
  cleanup();
});
afterAll(() => {
  server.close();
  vi.unstubAllGlobals();
});
