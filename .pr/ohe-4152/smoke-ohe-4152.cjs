// Browser check for OHE-4152 against the mock API. It also takes the PR
// screenshots, which land next to this file.
//
//   cd frontend
//   VITE_FRONTEND_PORT=3052 npm run dev:mock:saas
//   node ../.pr/ohe-4152/smoke-ohe-4152.cjs
//
// The mock API has no feature-flag endpoints and serves a fixed config. So
// the page answers PATCH and POST itself, and the script moves between pages
// without a reload, which keeps the config the switches wrote.
const path = require("path");

const frontendDir = path.resolve(__dirname, "../../frontend");
const { chromium } = require(
  path.join(frontendDir, "node_modules/@playwright/test"),
);

const BASE_URL = process.env.BASE_URL || "http://localhost:3052";
const AGENT_PROFILES = "super-admin-instance-enable-agent-profiles";
const ORG_MCP = "super-admin-instance-enable-org-mcp-servers";
const AGENT_PROFILES_NAV = 'a[href="/settings/agents"]';
const AGENT_CONTEXT_NAV = 'a[href="/settings/agent-context"]';
const ORG_MCP_SECTION = "org-mcp-servers-section";

// PATCH answers 404 until a POST has created the row, like the real API.
function fakeFeatureFlagApi() {
  const rows = {};
  const calls = [];
  window.flagCalls = calls;
  window.flagResponses = 0;
  const { open, send } = XMLHttpRequest.prototype;
  XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
    this.flagRequest = String(url).includes("/api/admin/feature-flags")
      ? { method: method.toUpperCase(), url: String(url) }
      : null;
    return open.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function patchedSend(body) {
    const request = this.flagRequest;
    if (!request) return send.call(this, body);
    const payload = JSON.parse(body);
    let status = 200;
    let response;
    if (request.method === "POST") {
      rows[payload.key] = { ...payload, rules: [] };
      status = 201;
      response = rows[payload.key];
    } else {
      const key = decodeURIComponent(request.url.split("/").pop());
      if (rows[key]) {
        rows[key] = { ...rows[key], ...payload };
        response = rows[key];
      } else {
        status = 404;
        response = { detail: "Flag not found" };
      }
    }
    calls.push(`${request.method} ${status} ${payload.key ?? ""}`.trim());
    const text = JSON.stringify(response);
    Object.entries({
      readyState: 4,
      status,
      statusText: status === 404 ? "Not Found" : "OK",
      responseText: text,
      response: text,
      responseURL: request.url,
    }).forEach(([name, value]) => {
      Object.defineProperty(this, name, { value, configurable: true });
    });
    this.getAllResponseHeaders = () => "content-type: application/json\r\n";
    setTimeout(() => {
      window.flagResponses += 1;
      this.dispatchEvent(new Event("readystatechange"));
      this.dispatchEvent(new ProgressEvent("load"));
      this.dispatchEvent(new ProgressEvent("loadend"));
    }, 50);
    return undefined;
  };
}

const results = [];
function check(name, ok) {
  results.push({ name, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
}

async function open(page, route, testId) {
  // The dev server may reload the page once while it optimizes dependencies.
  await page.goto(`${BASE_URL}${route}`).catch(() => undefined);
  await page.waitForTimeout(8000);
  await page.goto(`${BASE_URL}${route}`).catch(() => undefined);
  await page.getByTestId(testId).waitFor({ timeout: 60000 });
}

async function navigate(page, route, testId) {
  await page.evaluate((to) => {
    window.history.pushState({}, "", to);
    window.dispatchEvent(new PopStateEvent("popstate"));
  }, route);
  await page.getByTestId(testId).waitFor({ timeout: 30000 });
  await page.waitForTimeout(2000);
}

// The switch input is hidden, so it is clicked in the page. The save is done
// once the API has answered and the switch shows the new state, enabled again.
async function setSwitch(page, testId, on) {
  const answered = await page.evaluate(() => window.flagResponses);
  await page.getByTestId(testId).evaluate((el) => el.click());
  await page.waitForFunction(
    ([id, expected, before]) => {
      const input = document.querySelector(`[data-testid="${id}"]`);
      return (
        window.flagResponses > before &&
        input.checked === expected &&
        !input.disabled
      );
    },
    [testId, on, answered],
  );
  // Let the switch finish its transition before a screenshot.
  await page.waitForTimeout(500);
}

const screenshot = (page, name) =>
  page.screenshot({ path: path.join(__dirname, name) });

(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
  });
  await page.addInitScript(fakeFeatureFlagApi);

  await open(page, "/settings/mcp", "mcp-settings-screen");
  check(
    "before: Settings has no Agent Profiles, Agent Context or organization MCP servers",
    (await page.locator(AGENT_PROFILES_NAV).count()) === 0 &&
      (await page.locator(AGENT_CONTEXT_NAV).count()) === 0 &&
      (await page.getByTestId(ORG_MCP_SECTION).count()) === 0,
  );
  await screenshot(page, "settings-before.png");

  await open(page, "/super-admin/instance", "super-admin-instance");
  check(
    "both switches start off",
    !(await page.getByTestId(AGENT_PROFILES).isChecked()) &&
      !(await page.getByTestId(ORG_MCP).isChecked()),
  );
  await screenshot(page, "instance-switches-off.png");

  await setSwitch(page, AGENT_PROFILES, true);
  await setSwitch(page, ORG_MCP, true);
  check(
    "the first save of each switch creates its flag (PATCH 404, then POST)",
    JSON.stringify(await page.evaluate(() => window.flagCalls)) ===
      JSON.stringify([
        "PATCH 404",
        "POST 201 ENABLE_AGENT_PROFILES",
        "PATCH 404",
        "POST 201 ENABLE_ORG_MCP_SERVERS",
      ]),
  );
  await screenshot(page, "instance-switches-on.png");

  await navigate(page, "/settings/mcp", "mcp-settings-screen");
  check(
    "after: Settings shows Agent Profiles, Agent Context and organization MCP servers",
    (await page.locator(AGENT_PROFILES_NAV).count()) > 0 &&
      (await page.locator(AGENT_CONTEXT_NAV).count()) > 0 &&
      (await page.getByTestId(ORG_MCP_SECTION).count()) === 1,
  );
  await screenshot(page, "settings-after-switches-on.png");

  await navigate(page, "/super-admin/instance", "super-admin-instance");
  await setSwitch(page, AGENT_PROFILES, false);
  check(
    "a later change updates the flag (PATCH 200)",
    (await page.evaluate(() => window.flagCalls.at(-1))) === "PATCH 200",
  );
  await navigate(page, "/settings/mcp", "mcp-settings-screen");
  check(
    "Agent Profiles off hides its pages and leaves organization MCP servers on",
    (await page.locator(AGENT_PROFILES_NAV).count()) === 0 &&
      (await page.getByTestId(ORG_MCP_SECTION).count()) === 1,
  );

  await browser.close();
  const failed = results.filter((result) => !result.ok).length;
  console.log(`${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
