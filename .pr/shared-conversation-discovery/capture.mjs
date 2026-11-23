// Evidence capture for OHE-4205: the "Discover shared conversations" switch
// on Settings > Organization.
//
// Runs against the frontend's mock API, whose current organization (Acme Corp)
// is a team organization owned by the mock user. The mock
// `PATCH /api/organizations/:orgId` handler only accepts a name change, so the
// script records the request the switch sends rather than the mock's reply.
//
// Requires the mock dev server, started from frontend/:
//   VITE_DO_NOT_TRACK=1 VITE_MOCK_API=true VITE_MOCK_SAAS=true npx react-router dev --port 3197
// Usage: node .pr/shared-conversation-discovery/capture.mjs [baseUrl] [outputDir]
import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";

// Playwright is installed with the frontend's dependencies.
const require = createRequire(
  new URL("../../frontend/package.json", import.meta.url),
);
const { chromium } = require("@playwright/test");

const baseUrl = process.argv[2] ?? "http://127.0.0.1:3197";
const output = process.argv[3] ?? ".pr/shared-conversation-discovery";
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
page.setDefaultTimeout(30000);
await page.addInitScript(() => {
  localStorage.setItem("analytics-consent", "false");
  localStorage.setItem("openhands-telemetry-consent", "denied");
});

const updates = [];
page.on("request", (request) => {
  if (
    request.method() === "PATCH" &&
    /\/api\/organizations\/[^/]+$/.test(new URL(request.url()).pathname)
  ) {
    updates.push(
      `PATCH ${new URL(request.url()).pathname} ${request.postData()}`,
    );
  }
});

try {
  await page.goto(`${baseUrl}/settings/org`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByTestId("manage-org-screen").waitFor();
  const input = page.getByTestId("discover-shared-conversations-switch");
  await input.waitFor({ state: "attached" });
  const label = input.locator("xpath=ancestor::label");
  console.log(
    "organization:",
    await page.getByTestId("org-name").locator(".truncate").innerText(),
  );
  console.log("switch:", (await label.innerText()).trim());
  console.log("on by default:", await input.isChecked());
  await page.screenshot({ path: `${output}/settings-organization.png` });

  await label.click();
  await page.waitForTimeout(1500);
  console.log("request sent when turned off:", updates);
} finally {
  await browser.close();
}
