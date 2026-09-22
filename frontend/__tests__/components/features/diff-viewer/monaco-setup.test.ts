import { describe, it, expect, vi, beforeAll } from "vitest";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import JsonWorker from "monaco-editor/language/json/json.worker.js?worker";
import CssWorker from "monaco-editor/language/css/css.worker.js?worker";
import HtmlWorker from "monaco-editor/language/html/html.worker.js?worker";
import TsWorker from "monaco-editor/language/typescript/ts.worker.js?worker";

/** Records `loader.config` calls from the mocked `@monaco-editor/react`. */
const loaderCalls = () =>
  (globalThis as unknown as { __loaderCalls: unknown[][] }).__loaderCalls;

vi.mock("monaco-editor", () => ({ editor: {}, languages: {} }));

vi.mock("@monaco-editor/react", () => {
  const g = globalThis as unknown as { __loaderCalls?: unknown[][] };
  g.__loaderCalls = [];
  return {
    loader: {
      config: (...args: unknown[]) => {
        g.__loaderCalls!.push(args);
      },
    },
  };
});

vi.mock("monaco-editor/editor/editor.worker.js?worker", () => ({
  default: class MockEditorWorker {},
}));

vi.mock("monaco-editor/language/json/json.worker.js?worker", () => ({
  default: class MockJsonWorker {},
}));

vi.mock("monaco-editor/language/css/css.worker.js?worker", () => ({
  default: class MockCssWorker {},
}));

vi.mock("monaco-editor/language/html/html.worker.js?worker", () => ({
  default: class MockHtmlWorker {},
}));

vi.mock("monaco-editor/language/typescript/ts.worker.js?worker", () => ({
  default: class MockTsWorker {},
}));

const getWorker = (label: string) =>
  globalThis.MonacoEnvironment?.getWorker?.("test-worker-id", label);

describe("monaco-setup", () => {
  beforeAll(async () => {
    // The module under test does all of its work as import side effects, so it
    // must be imported after the mocks above are registered.
    await import("#/components/features/diff-viewer/monaco-setup");
  });

  it("points the @monaco-editor/react loader at the bundled monaco instance", () => {
    // With a monaco instance configured, loader.init() resolves with it and
    // never injects the cdn.jsdelivr.net <script> tag, which the CSP
    // (script-src 'self' ...) blocks and air-gapped installs cannot reach.
    expect(loaderCalls().length).toBeGreaterThanOrEqual(1);
    expect(
      (loaderCalls().at(-1)?.[0] as { monaco: unknown }).monaco,
    ).toBe(monaco);
  });

  it("routes json language services to the bundled json worker", () => {
    expect(getWorker("json")).toBeInstanceOf(JsonWorker);
  });

  it("routes css-family languages to the bundled css worker", () => {
    expect(getWorker("css")).toBeInstanceOf(CssWorker);
    expect(getWorker("scss")).toBeInstanceOf(CssWorker);
    expect(getWorker("less")).toBeInstanceOf(CssWorker);
  });

  it("routes html-family languages to the bundled html worker", () => {
    expect(getWorker("html")).toBeInstanceOf(HtmlWorker);
    expect(getWorker("handlebars")).toBeInstanceOf(HtmlWorker);
    expect(getWorker("razor")).toBeInstanceOf(HtmlWorker);
  });

  it("routes typescript and javascript to the bundled ts worker", () => {
    expect(getWorker("typescript")).toBeInstanceOf(TsWorker);
    expect(getWorker("javascript")).toBeInstanceOf(TsWorker);
  });

  it("falls back to the core editor worker for any other label", () => {
    // "editorWorkerService" is what monaco requests for diff computation.
    expect(getWorker("editorWorkerService")).toBeInstanceOf(EditorWorker);
    expect(getWorker("python")).toBeInstanceOf(EditorWorker);
  });
});
