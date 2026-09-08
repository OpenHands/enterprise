import * as monaco from "monaco-editor";
import { loader } from "@monaco-editor/react";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import CssWorker from "monaco-editor/esm/vs/language/css/css.worker?worker";
import HtmlWorker from "monaco-editor/esm/vs/language/html/html.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";

// OHE-3068: self-host Monaco instead of loading it from cdn.jsdelivr.net,
// which the CSP (script-src 'self' ...) blocks and air-gapped installs
// cannot reach. Bundling the direct `monaco-editor` dependency keeps every
// editor asset same-origin.
//
// Monaco spawns web workers for diff computation and language services;
// wire them to Vite-bundled same-origin worker chunks per Monaco's
// bundler guidance. Labels are language ids; anything unmatched uses the
// core editor worker.
globalThis.MonacoEnvironment = {
  getWorker(_workerId: string, label: string): Worker {
    switch (label) {
      case "json":
        return new JsonWorker();
      case "css":
      case "scss":
      case "less":
        return new CssWorker();
      case "html":
      case "handlebars":
      case "razor":
        return new HtmlWorker();
      case "typescript":
      case "javascript":
        return new TsWorker();
      default:
        return new EditorWorker();
    }
  },
};

// With a monaco instance configured, loader.init() resolves with it and
// never injects the jsdelivr <script> tag.
loader.config({ monaco });
