/*
 * Headless smoke test for OHE-3957 against the enterprise frontend mock API.
 *
 *   cd frontend
 *   VITE_MOCK_ORG_MCP_SERVERS=true VITE_FRONTEND_PORT=3027 npm run dev:mock:saas
 *   node ../.pr/ohe-3957/smoke-ohe-3957.cjs
 *
 * Set BEFORE=1 to take the "before" screenshots against a build without the
 * fix (no assertions, same stubs).
 */
const {
  chromium,
} = require("../../frontend/node_modules/@playwright/test");

const BASE = process.env.BASE || "http://localhost:3027";
const OUT = __dirname;

const GITHUB_ROW = {
  id: 1,
  title: "GitHub",
  icon: null,
  color: "#000000",
  provider_type: "github",
  connected: true,
  needs_reconnect: false,
};
const GITLAB_ROW = {
  id: 2,
  title: "GitLab",
  icon: null,
  color: "#000000",
  provider_type: "gitlab",
  connected: false,
  needs_reconnect: false,
};

/**
 * Runs in the page. The mock API is an MSW service worker, so the config is
 * patched on the XHR instance, and `/oauth/git-providers` (which the mock does
 * not serve) is answered here.
 */
function initScript(opts) {
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  const textDesc = Object.getOwnPropertyDescriptor(
    XMLHttpRequest.prototype,
    "responseText",
  );
  const patchConfig = (raw) => {
    try {
      const json = JSON.parse(raw);
      json.providers_configured = opts.providersConfigured;
      json.feature_flags = {
        ...json.feature_flags,
        enable_oauth_v2_login: opts.oauthV2,
      };
      return JSON.stringify(json);
    } catch {
      return raw;
    }
  };
  XMLHttpRequest.prototype.open = function open(method, url, ...rest) {
    this.__u = String(url);
    const path = new URL(this.__u, location.origin).pathname;
    if (path === "/api/v1/web-client/config") {
      Object.defineProperty(this, "responseText", {
        configurable: true,
        get() {
          return patchConfig(textDesc.get.call(this));
        },
      });
    }
    return origOpen.call(this, method, url, ...rest);
  };
  XMLHttpRequest.prototype.send = function send(body) {
    const u = new URL(this.__u, location.origin);
    if (u.pathname !== "/oauth/git-providers") {
      return origSend.call(this, body);
    }
    sessionStorage.setItem(
      "__gitProviderCalls",
      String(Number(sessionStorage.getItem("__gitProviderCalls") || 0) + 1),
    );
    const text = JSON.stringify(opts.gitProviders);
    const define = (k, v) =>
      Object.defineProperty(this, k, { value: v, configurable: true });
    setTimeout(() => {
      define("readyState", 4);
      define("status", 200);
      define("statusText", "OK");
      define("responseText", text);
      define("response", text);
      define(
        "getAllResponseHeaders",
        () => "content-type: application/json\r\n",
      );
      define("getResponseHeader", (h) =>
        h.toLowerCase() === "content-type" ? "application/json" : null,
      );
      this.dispatchEvent(new Event("readystatechange"));
      this.dispatchEvent(new ProgressEvent("load"));
      this.dispatchEvent(new ProgressEvent("loadend"));
    }, opts.gitProvidersDelayMs || 0);
    return undefined;
  };
}

let failures = 0;
function check(label, condition, detail = "") {
  if (condition) {
    console.log(`  ok: ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL: ${label} ${detail}`);
  }
}

async function openCard(page, id) {
  await page.getByTestId("mcp-library-search").fill(id);
  await page.getByTestId(`mcp-library-card-${id}`).click();
  const modal = page.getByTestId("mcp-install-modal");
  await modal.waitFor();
  return modal;
}

async function closeModal(page) {
  await page.keyboard.press("Escape");
  await page.getByTestId("mcp-install-modal").waitFor({ state: "detached" });
}

async function scenario(browser, name, opts, run, { settle = false } = {}) {
  console.log(`\n== ${name}`);
  const context = await browser.newContext({
    viewport: { width: 1280, height: 1000 },
  });
  await context.addInitScript(initScript, opts);
  const page = await context.newPage();
  page.on("pageerror", (e) => console.log("  pageerror:", e.message));
  await page.goto(`${BASE}/settings/mcp`, { waitUntil: "networkidle" });
  if (settle) {
    // Vite optimizes deps on the first request and reloads; settle, then reload.
    await page.waitForTimeout(8000);
    await page.reload({ waitUntil: "networkidle" });
  }
  await page.getByTestId("mcp-library-section").waitFor({ timeout: 30000 });
  try {
    await run(page);
  } catch (error) {
    failures += 1;
    console.log(`  FAIL: threw: ${error.message.split("\n")[0]}`);
  }
  await context.close();
}

const gitProviderCalls = (page) =>
  page.evaluate(() =>
    Number(sessionStorage.getItem("__gitProviderCalls") || 0),
  );

// OAuth v2 login on; the chart enables GitHub and GitLab, but a super admin
// enabled only GitHub under Auth Providers. The mock user has GitHub connected.
const MISMATCH = {
  oauthV2: true,
  providersConfigured: ["github", "gitlab"],
  gitProviders: [GITHUB_ROW],
};

async function before(browser) {
  await scenario(
    browser,
    "Before: OAuth v2 on, Auth Providers has only GitHub",
    MISMATCH,
    async (page) => {
      let modal = await openCard(page, "github");
      await modal.getByTestId("mcp-native-panel").waitFor();
      await modal.screenshot({ path: `${OUT}/before-github-native.png` });
      await modal.getByTestId("mcp-native-continue").click();
      await page
        .getByTestId("mcp-install-modal")
        .waitFor({ state: "detached" });
      console.log(`  Continue closed the dialog; still on ${page.url()}`);

      modal = await openCard(page, "gitlab");
      await modal.getByTestId("mcp-native-panel").waitFor();
      await modal.screenshot({ path: `${OUT}/before-gitlab-native-offered.png` });
    },
    { settle: true },
  );
}

async function after(browser) {
  await scenario(
    browser,
    "A. OAuth v2 on: chart has github+gitlab, Auth Providers has only GitHub (connected)",
    MISMATCH,
    async (page) => {
      let modal = await openCard(page, "github");
      await modal.getByTestId("mcp-install-tab-native").waitFor();
      const tab = await modal.getByTestId("mcp-install-tab-native").innerText();
      check(
        "GitHub tab reads 'Built-in GitHub Integration'",
        tab === "Built-in GitHub Integration",
        `(got ${JSON.stringify(tab)})`,
      );
      const panel = await modal.getByTestId("mcp-native-panel").innerText();
      check(
        "panel says connected through the built-in integration",
        panel.includes("GitHub is connected through the built-in integration."),
      );
      check(
        "dialog no longer says 'native'",
        !/native/i.test(await modal.innerText()),
      );
      check(
        "GitHub MCP tab is still offered",
        (await modal.getByTestId("mcp-install-tab-mcp").count()) === 1,
      );
      await modal.screenshot({ path: `${OUT}/after-github-built-in.png` });
      await closeModal(page);

      modal = await openCard(page, "gitlab");
      await modal.getByTestId("mcp-install-submit").waitFor();
      check(
        "GitLab (not under Auth Providers) has no Built-in tab",
        (await modal.getByTestId("mcp-install-tab-native").count()) === 0,
      );
      check(
        "GitLab opens the MCP form directly",
        await modal.getByTestId("mcp-install-submit").isVisible(),
      );
      await modal.screenshot({ path: `${OUT}/after-gitlab-mcp-form.png` });
      await closeModal(page);

      modal = await openCard(page, "github");
      await modal.getByTestId("mcp-native-continue").click();
      await page.waitForURL(
        (url) => url.pathname === "/settings/integrations",
        { timeout: 10000 },
      );
      await page.getByTestId("git-settings-screen").waitFor({ timeout: 15000 });
      check(
        "Continue lands on Settings > Integrations",
        new URL(page.url()).pathname === "/settings/integrations",
      );
      check(
        "the dialog is gone",
        (await page.getByTestId("mcp-install-modal").count()) === 0,
      );
      await page.waitForTimeout(500);
      await page.screenshot({
        path: `${OUT}/after-continue-opens-integrations.png`,
        clip: { x: 0, y: 0, width: 1280, height: 420 },
      });
    },
    { settle: true },
  );

  await scenario(
    browser,
    "B. OAuth v2 on: chart has no providers, Auth Providers has only GitLab (not connected)",
    { oauthV2: true, providersConfigured: [], gitProviders: [GITLAB_ROW] },
    async (page) => {
      let modal = await openCard(page, "gitlab");
      await modal.getByTestId("mcp-install-tab-native").waitFor();
      check(
        "GitLab tab reads 'Built-in GitLab Integration'",
        (await modal.getByTestId("mcp-install-tab-native").innerText()) ===
          "Built-in GitLab Integration",
      );
      check(
        "not connected: Install is offered, Continue is not",
        (await modal.getByTestId("mcp-native-install").isVisible()) &&
          (await modal.getByTestId("mcp-native-continue").count()) === 0,
      );
      await closeModal(page);

      modal = await openCard(page, "github");
      await modal.getByTestId("mcp-install-submit").waitFor();
      check(
        "GitHub (not under Auth Providers) has no Built-in tab",
        (await modal.getByTestId("mcp-install-tab-native").count()) === 0,
      );
    },
  );

  await scenario(
    browser,
    "C. OAuth v2 off: the tab still follows providers_configured",
    {
      oauthV2: false,
      providersConfigured: ["github"],
      gitProviders: [GITLAB_ROW],
    },
    async (page) => {
      let modal = await openCard(page, "github");
      await modal.getByTestId("mcp-install-tab-native").waitFor();
      check(
        "GitHub (in providers_configured) has the Built-in tab",
        (await modal.getByTestId("mcp-install-tab-native").count()) === 1,
      );
      await closeModal(page);

      modal = await openCard(page, "gitlab");
      await modal.getByTestId("mcp-install-submit").waitFor();
      check(
        "GitLab (not in providers_configured) has no Built-in tab",
        (await modal.getByTestId("mcp-install-tab-native").count()) === 0,
      );
      check(
        "/oauth/git-providers is never requested",
        (await gitProviderCalls(page)) === 0,
      );
    },
  );

  await scenario(
    browser,
    "D. OAuth v2 on: a slow Auth Providers list shows a loading line, not the MCP form",
    {
      oauthV2: true,
      providersConfigured: [],
      gitProviders: [GITHUB_ROW],
      gitProvidersDelayMs: 2500,
    },
    async (page) => {
      const modal = await openCard(page, "github");
      await modal
        .getByTestId("mcp-install-resolving")
        .waitFor({ timeout: 2000 });
      check(
        "no MCP form and no tabs while the list loads",
        (await modal.getByTestId("mcp-install-submit").count()) === 0 &&
          (await modal.getByTestId("mcp-install-tab-native").count()) === 0,
      );
      await modal
        .getByTestId("mcp-install-tab-native")
        .waitFor({ timeout: 6000 });
      check(
        "the Built-in tab replaces the loading line",
        (await modal.getByTestId("mcp-install-resolving").count()) === 0,
      );
      await closeModal(page);

      const slack = await openCard(page, "slack");
      check(
        "a non-git entry never waits on the list",
        (await slack.getByTestId("mcp-install-resolving").count()) === 0,
      );
    },
  );
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  if (process.env.BEFORE) {
    await before(browser);
  } else {
    await after(browser);
  }
  await browser.close();
  console.log(failures === 0 ? "\nSMOKE PASSED" : `\nSMOKE FAILED (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
})();
