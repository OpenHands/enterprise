// Smoke test for OHE-4129 against the mock API: a "Hide the Setup guide?"
// question left open when a tour starts came back when the tour ended.
//
//   cd frontend
//   VITE_FRONTEND_PORT=3031 npm run dev:mock:saas
//   node ../.pr/ohe-4129/smoke.cjs <label>
//
// The screenshots are saved next to this file as <label>-<name>.png.
// BASE_URL overrides http://localhost:3031. Uses the installed Chrome.
const path = require("path");
const { chromium } = require("../../frontend/node_modules/@playwright/test");

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3031";
const LABEL = process.argv[2] || "run";

function seed() {
  localStorage.setItem(
    "oh-sa-nux",
    JSON.stringify({
      welcomeDone: true,
      companyDone: true,
      orgDone: true,
      starterModalPending: false,
      company: { name: "Acme", hasLogo: false },
      org: { name: "Acme" },
    }),
  );
  // Only the LLM and invite steps are left, so the invite step's tour, which
  // runs in this app, follows the LLM step.
  const state = () =>
    JSON.stringify({
      wizard_pending: false,
      guide_org_id: "2",
      guide_dismissed: false,
      guide_steps: {
        org_llm: Boolean(sessionStorage.getItem("llm-done")),
        git_provider: true,
        mcp_server: true,
        automation: true,
        invite: false,
      },
    });
  const { open } = XMLHttpRequest.prototype;
  XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
    this.ohUrl = String(url);
    this.ohMethod = String(method).toUpperCase();
    return open.call(this, method, url, ...rest);
  };
  for (const prop of ["response", "responseText"]) {
    const desc = Object.getOwnPropertyDescriptor(
      XMLHttpRequest.prototype,
      prop,
    );
    Object.defineProperty(XMLHttpRequest.prototype, prop, {
      configurable: true,
      get() {
        if (
          this.ohMethod === "GET" &&
          this.ohUrl &&
          this.ohUrl.includes("/api/admin/setup-state")
        ) {
          return state();
        }
        return desc.get.call(this);
      },
    });
  }
}

async function look(page, name) {
  const seen = await page.evaluate(() => {
    const popover = document.querySelector(".driver-popover");
    const toggle = document.querySelector(
      '[data-testid="super-admin-setup-floating-toggle"]',
    );
    return {
      path: window.location.pathname,
      question: Boolean(
        document.querySelector('[data-testid="confirmation-modal"]'),
      ),
      tourStop:
        popover && getComputedStyle(popover).display !== "none"
          ? popover.querySelector(".driver-popover-title").textContent
          : null,
      setupGuide: Boolean(
        document.querySelector('[data-testid="super-admin-setup-floating"]'),
      ),
      panelOpen: toggle ? toggle.getAttribute("aria-expanded") : null,
    };
  });
  console.log(`[${LABEL}] ${name}: ${JSON.stringify(seen)}`);
  await page.screenshot({ path: path.join(__dirname, `${LABEL}-${name}.png`) });
  return seen;
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  await context.addInitScript(seed);
  const page = await context.newPage();
  const start = `${BASE_URL}/super-admin/organizations?org=2`;

  // Vite optimizes dependencies on the first request; retry until it serves.
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      await page.goto(start, { waitUntil: "domcontentloaded", timeout: 15000 });
      await page.waitForTimeout(4000);
      await page.goto(start, { waitUntil: "domcontentloaded", timeout: 15000 });
      await page
        .getByTestId("super-admin-setup-floating-panel")
        .waitFor({ timeout: 20000 });
      break;
    } catch (error) {
      if (attempt === 19) throw error;
      await page.waitForTimeout(3000);
    }
  }

  // The user closes the panel and leaves the question unanswered.
  await page
    .getByTestId("super-admin-setup-floating-panel")
    .getByRole("button", { name: "Close" })
    .click();
  await page.getByTestId("confirmation-modal").waitFor({ timeout: 2000 });
  await look(page, "question-asked");

  // The LLM step succeeds, so the guide opens the invite step's tour.
  await page.evaluate(() => {
    sessionStorage.setItem("llm-done", "1");
    window.dispatchEvent(
      new CustomEvent("oh-super-admin-setup-step", {
        detail: { id: "add-llm" },
      }),
    );
  });
  await page.locator(".driver-popover").waitFor({ timeout: 20000 });
  await page.waitForTimeout(600);
  await look(page, "tour-started");

  await page.locator(".driver-popover-close-btn").click();
  await page.waitForTimeout(1000);
  await look(page, "tour-closed");

  await browser.close();
})().catch((error) => {
  console.error("SMOKE FAILED:", error);
  process.exit(1);
});
