// Browser check for OHE-3717 against the mock API.
//
//   cd frontend
//   VITE_FRONTEND_PORT=3017 npm run dev:mock:saas
//   node ../.pr/ohe-3717/smoke-ohe-3717.cjs
//
// The mock API knows nothing about the default-org fallback, so the script
// rewrites two of its responses in the page: the web-client config (personal
// workspaces hidden) and the organization list (one team org for the user,
// plus `default_org_fallback_active`). Screenshots land next to this file.
const path = require("node:path");

const { chromium } = require(
  path.join(process.cwd(), "node_modules/@playwright/test"),
);

const BASE_URL = process.env.SMOKE_BASE_URL || "http://localhost:3017";

function patchResponses({ fallbackActive }) {
  const origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function open(method, url, ...rest) {
    this.smokeUrl = String(url);
    this.smokeMethod = String(method).toUpperCase();
    return origOpen.call(this, method, url, ...rest);
  };
  const rewrite = (xhr, text) => {
    try {
      const { pathname } = new URL(xhr.smokeUrl, window.location.origin);
      if (xhr.smokeMethod !== "GET") return text;
      if (pathname === "/api/organizations") {
        const body = JSON.parse(text);
        // Personal workspace + Acme Corp: one team org for this user.
        body.items = body.items.filter((o) => o.id === "1" || o.id === "2");
        body.default_org_fallback_active = fallbackActive;
        return JSON.stringify(body);
      }
      if (pathname === "/api/v1/web-client/config") {
        const body = JSON.parse(text);
        body.feature_flags = {
          ...body.feature_flags,
          hide_personal_workspaces: true,
        };
        return JSON.stringify(body);
      }
    } catch {
      // Leave the response untouched.
    }
    return text;
  };
  for (const prop of ["responseText", "response"]) {
    const desc = Object.getOwnPropertyDescriptor(
      XMLHttpRequest.prototype,
      prop,
    );
    Object.defineProperty(XMLHttpRequest.prototype, prop, {
      configurable: true,
      get() {
        const value = desc.get.call(this);
        return typeof value === "string" ? rewrite(this, value) : value;
      },
    });
  }
}

async function open(page, url, readyTestId) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  // Vite optimizes dependencies on the first visit and reloads the page.
  await page.waitForTimeout(8000);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByTestId(readyTestId).waitFor({ timeout: 60000 });
}

async function runScenario(browser, fallbackActive) {
  const tag = fallbackActive ? "fallback-on" : "fallback-off";
  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
  });
  const page = await context.newPage();
  await page.addInitScript(patchResponses, { fallbackActive });

  await open(page, `${BASE_URL}/settings/org?org=2`, "manage-org-screen");
  await page.getByTestId("org-name").waitFor();
  await page.waitForTimeout(2000);
  const claimsShown =
    (await page.getByTestId("git-conversation-routing").count()) > 0;
  await page.screenshot({
    path: path.join(__dirname, `manage-org-${tag}.png`),
  });

  await open(
    page,
    `${BASE_URL}/super-admin/organizations`,
    "super-admin-organizations",
  );
  await page.getByTestId("super-admin-create-org").click();
  await page.getByTestId("create-organization-form").waitFor();
  await page.waitForTimeout(1500);
  const warningShown =
    (await page.getByTestId("create-organization-routing-warning").count()) >
    0;
  await page.screenshot({
    path: path.join(__dirname, `create-org-${tag}.png`),
  });

  await context.close();
  return { claimsShown, warningShown };
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  let failed = false;
  const check = (ok, label) => {
    failed = failed || !ok;
    console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  };

  try {
    const on = await runScenario(browser, true);
    check(
      !on.claimsShown,
      "fallback on: Settings > Organization hides Git Conversation Routing",
    );
    check(
      on.warningShown,
      "fallback on: Create organization shows the routing warning",
    );

    const off = await runScenario(browser, false);
    check(
      off.claimsShown,
      "fallback off: Settings > Organization shows Git Conversation Routing",
    );
    check(
      !off.warningShown,
      "fallback off: Create organization shows no routing warning",
    );
  } finally {
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
