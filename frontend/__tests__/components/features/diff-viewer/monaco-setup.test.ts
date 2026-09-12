import { describe, it, expect, vi } from "vitest";
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/language/json/json.worker?worker";
import CssWorker from "monaco-editor/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/language/html/html.worker?worker";
import TsWorker from "monaco-editor/language/typescript/ts.worker?worker";
// The module under test does all of its work as import side effects.
import "#/components/features/diff-viewer/monaco-setup";

vi.mock("monaco-editor", () => ({ editor: {}, languages: {} }));

vi.mock("@monaco-editor/react", () => ({
  loader: { config: vi.fn() },
}));

vi.mock("monaco-editor/editor/editor.worker?worker", () => ({
  default: class MockEditorWorker {},
}));

vi.mock("monaco-editor/language/json/json.worker?worker", () => ({
  default: class MockJsonWorker {},
}));

vi.mock("monaco-editor/language/css/css.worker?worker", () => ({
  default: class MockCssWorker {},
}));

vi.mock("monaco-editor/language/html/html.worker?worker", () => ({
  default: class MockHtmlWorker {},
}));

vi.mock("monaco-editor/language/typescript/ts.worker?worker", () => ({
  default: class MockTsWorker {},
}));

const getWorker = (label: string) =>
  globalThis.MonacoEnvironment?.getWorker?.("test-worker-id", label);

describe("monaco-setup", () => {
  it("points the @monaco-editor/react loader at the bundled monaco instance", () => {
    // With a monaco instance configured, loader.init() resolves with it and
    // never injects the cdn.jsdelivr.net <script> tag, which the CSP
    // (script-src 'self' ...) blocks and air-gapped installs cannot reach.
    expect(loader.config).toHaveBeenCalledTimes(1);
    expect(vi.mocked(loader.config).mock.lastCall?.[0]?.monaco).toBe(monaco);
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
