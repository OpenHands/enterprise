import fs from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";

const CANVAS_PREFIX = "/canvas";

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".wav": "audio/wav",
  ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json",
  ".wasm": "application/wasm",
};

/**
 * Serve the pre-built Agent Canvas SPA at /canvas from the Vite dev server.
 *
 * In cloud, /canvas is a separate deployment routed by an ingress rule. For
 * local development there is no ingress, so the bundle staged in
 * `public/canvas` (see `scripts/build-agent-canvas.sh`) is served here. Same-
 * origin `/api` calls are handled by the proxy already configured below.
 *
 * This is temporary scaffolding: once the OSS frontend is retired, /canvas is
 * the only surface and this plugin goes away.
 */
export function serveAgentCanvas(): Plugin {
  const canvasDir = path.resolve(process.cwd(), "public", "canvas");

  return {
    name: "serve-agent-canvas",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const urlPath = (req.url ?? "/").split("?")[0];
        if (
          urlPath !== CANVAS_PREFIX &&
          !urlPath.startsWith(`${CANVAS_PREFIX}/`)
        ) {
          next();
          return;
        }

        if (!fs.existsSync(canvasDir)) {
          next();
          return;
        }

        const relative = urlPath.slice(CANVAS_PREFIX.length) || "/";
        const safePath = path.normalize(relative).replace(/^(\.\.[/\\])+/, "");
        let filePath = path.join(canvasDir, safePath);

        // Deep links (client-side routes) and directories fall back to the
        // Canvas SPA shell.
        if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
          filePath = path.join(canvasDir, "index.html");
        }

        if (!fs.existsSync(filePath)) {
          next();
          return;
        }

        const extension = path.extname(filePath).toLowerCase();
        res.setHeader(
          "Content-Type",
          MIME_TYPES[extension] ?? "application/octet-stream",
        );
        fs.createReadStream(filePath).pipe(res);
      });
    },
  };
}
