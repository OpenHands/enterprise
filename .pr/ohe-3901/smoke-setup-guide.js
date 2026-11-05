/* Screenshots of the enterprise Setup guide on the mock server (port 3001).
 * MODE=before (main) only captures; MODE=after also checks that Start opens
 * Agent Canvas's MCP page asking for the connect-git tour. */
const { chromium } = require("/Users/hieple/Documents/openhands/OpenHands/frontend/node_modules/@playwright/test");
const OUT = process.env.OUT || "/private/tmp/claude-501/-Users-hieple-Documents-openhands-OpenHands/42354342-2c22-438d-ac9a-3cb1f36a89f0/scratchpad";
const MODE = process.env.MODE || "after";
const BASE = "http://localhost:3001";

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await context.addInitScript(() => {
    localStorage.setItem(
      "oh-sa-nux",
      JSON.stringify({ welcomeDone: true, companyDone: true, orgDone: true, starterModalPending: false, company: { name: "Acme", hasLogo: false }, org: { name: "Acme" } }),
    );
    const STATE = JSON.stringify({
      wizard_pending: false,
      guide_org_id: "2",
      guide_dismissed: false,
      guide_steps: { org_llm: true, git_provider: false, mcp_server: false, automation: false, invite: false },
    });
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this.__ohUrl = String(url);
      this.__ohMethod = String(method).toUpperCase();
      return open.call(this, method, url, ...rest);
    };
    for (const prop of ["response", "responseText"]) {
      const desc = Object.getOwnPropertyDescriptor(XMLHttpRequest.prototype, prop);
      Object.defineProperty(XMLHttpRequest.prototype, prop, {
        configurable: true,
        get() {
          if (this.__ohMethod === "GET" && this.__ohUrl && this.__ohUrl.includes("/api/admin/setup-state")) return STATE;
          return desc.get.call(this);
        },
      });
    }
  });
  const page = await context.newPage();

  // Vite optimizes deps on the first request; retry until the app serves.
  for (let i = 0; i < 40; i += 1) {
    try {
      await page.goto(`${BASE}/super-admin/organizations?org=2`, { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.waitForTimeout(4000);
      await page.goto(`${BASE}/super-admin/organizations?org=2`, { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.getByTestId("super-admin-setup-floating-panel").waitFor({ timeout: 20000 });
      break;
    } catch (e) {
      if (i === 39) throw e;
      await page.waitForTimeout(3000);
    }
  }

  const panel = page.getByTestId("super-admin-setup-floating-panel");
  const stepIds = await panel.locator('[data-testid^="super-admin-setup-floating-step-"]').evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-testid").replace("super-admin-setup-floating-step-", "")),
  );
  const progress = await panel.locator("span.tabular-nums").innerText();
  const next = await page.getByTestId("super-admin-setup-floating-next").innerText();
  console.log(`[${MODE}] floating steps:`, JSON.stringify(stepIds));
  console.log(`[${MODE}] progress:`, progress, "| next:", next);
  await panel.screenshot({ path: `${OUT}/${MODE}-floating-panel.png` });

  if (MODE === "after") {
    // Start opens the next step: Connect Git, in Agent Canvas, with its tour.
    await page.getByTestId("super-admin-setup-floating-guide").click();
    await page.waitForURL((url) => url.pathname === "/canvas/mcp", { timeout: 15000 });
    console.log(`[${MODE}] start opened:`, page.url());
  }

  // The full setup page lists each step with its hint.
  await page.goto(`${BASE}/super-admin/setup?org=2`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.getByTestId("super-admin-setup-step-add-llm").waitFor({ timeout: 20000 });
  const pageIds = await page.locator('[data-testid^="super-admin-setup-step-"]').evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-testid").replace("super-admin-setup-step-", "")),
  );
  console.log(`[${MODE}] setup page steps:`, JSON.stringify(pageIds));
  await page.screenshot({ path: `${OUT}/${MODE}-setup-page.png`, fullPage: false });
  await browser.close();
})().catch((e) => {
  console.error("SMOKE FAILED:", e);
  process.exit(1);
});
