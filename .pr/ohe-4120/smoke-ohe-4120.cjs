// Smoke test for OHE-4120 against the mock API: the MCP Library dialog on
// Settings > MCP offers the Personal / Organization scope for stdio servers,
// and Organization hands the entry to "Add organization server" prefilled.
//
//   cd frontend
//   VITE_MOCK_ORG_MCP_SERVERS=true VITE_FRONTEND_PORT=3041 npm run dev:mock:saas
//   node ../.pr/ohe-4120/smoke-ohe-4120.cjs
//
// BEFORE=1 only takes the "before" screenshot (run it on a checkout without
// the fix). BASE_URL overrides http://localhost:3041.
//
// The mock user owns Acme Corp (org 2), is a member of Beta LLC (org 3) and
// has a personal workspace (org 1).
const path = require("path");
const { chromium } = require("../../frontend/node_modules/@playwright/test");

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3041";
const BEFORE = process.env.BEFORE === "1";

const results = [];
function check(name, ok, detail = "") {
  results.push(ok);
  const suffix = detail ? `  [${detail}]` : "";
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${suffix}`);
}

const shot = (locator, name) =>
  locator.screenshot({ path: path.join(__dirname, `${name}.png`) });

async function openSettings(page, org) {
  await page.goto(`${BASE_URL}/settings/mcp?org=${org}`, {
    waitUntil: "domcontentloaded",
  });
  await page.getByTestId("mcp-library-search").waitFor({ timeout: 60000 });
}

async function openEntry(page, id) {
  await page.getByTestId("mcp-library-search").fill(id);
  await page.getByTestId(`mcp-library-card-${id}`).click();
  const modal = page.getByTestId("mcp-install-modal");
  await modal.waitFor();
  return modal;
}

async function openOrgForm(page, modal) {
  await modal.getByTestId("mcp-install-submit").click();
  const form = page.getByTestId("add-org-mcp-server-modal");
  await form.waitFor();
  return form;
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });

  // On a cold start Vite optimizes dependencies during the first request and
  // reloads the page, so load it once and let that settle.
  await page
    .goto(`${BASE_URL}/settings/mcp?org=2`, { waitUntil: "domcontentloaded" })
    .catch(() => {});
  await page.waitForTimeout(8000);
  await openSettings(page, 2);

  // Tavily only has a stdio connection method.
  let modal = await openEntry(page, "tavily");
  await modal.getByTestId("mcp-install-field-TAVILY_API_KEY").waitFor();
  const scope = modal.getByTestId("mcp-install-scope-select");

  if (BEFORE) {
    check("Before: Tavily has no Scope select", (await scope.count()) === 0);
    await shot(modal, "before-tavily-no-scope");
    await browser.close();
    process.exit(results.every(Boolean) ? 0 : 1);
  }

  check("Tavily shows the Scope select", await scope.isVisible());
  check(
    "Scope defaults to Personal",
    (await scope.inputValue()) === "personal",
  );
  await shot(modal, "after-tavily-scope-personal");

  await scope.selectOption("org");
  check(
    "Organization hides the personal credential field",
    (await modal.getByTestId("mcp-install-field-TAVILY_API_KEY").count()) === 0,
  );
  const submitLabel = (
    await modal.getByTestId("mcp-install-submit").innerText()
  ).trim();
  check("The button reads Continue", submitLabel === "Continue", submitLabel);
  await shot(modal, "after-tavily-scope-organization");

  let form = await openOrgForm(page, modal);
  const value = (testId) => form.getByTestId(testId).inputValue();
  check(
    "The Library dialog closes",
    (await page.getByTestId("mcp-install-modal").count()) === 0,
  );
  const type = await value("org-server-type-dropdown");
  check("Form: server type STDIO", /stdio/i.test(type), type);
  check("Form: name", (await value("org-name-input")) === "tavily");
  check("Form: command", (await value("org-command-input")) === "npx");
  check(
    "Form: arguments",
    (await value("org-args-input")) === "-y\ntavily-mcp",
  );
  const env = await value("org-env-input");
  check(
    "Form: the required variable references a secret",
    env === "TAVILY_API_KEY=${TAVILY_API_KEY}",
    env,
  );
  const managedBy = await value("org-managed-by-dropdown");
  check(
    "Form: credential source is a personal secret",
    /personal secret/i.test(managedBy),
    managedBy,
  );
  await shot(form, "after-tavily-organization-form");

  await form.getByTestId("org-submit-button").click();
  await form.waitFor({ state: "detached" });
  const row = page
    .getByTestId("org-mcp-server-item")
    .filter({ hasText: "Tavily" });
  await row.waitFor();
  const rowText = (await row.innerText()).replace(/\s+/g, " ");
  check(
    "The listing shows the stdio command",
    rowText.includes("npx -y tavily-mcp"),
    rowText,
  );
  check("The listing needs connection", /needs connection/i.test(rowText));
  await shot(
    page.getByTestId("org-mcp-server-list"),
    "after-tavily-organization-listing",
  );

  // Sentry has a remote method (OAuth) and a stdio one (API key or token).
  modal = await openEntry(page, "sentry");
  const methods = modal.locator('[data-testid^="mcp-install-option-"]');
  const personalMethods = await methods.count();
  await modal.getByTestId("mcp-install-option-api").click();
  await modal.getByTestId("mcp-install-scope-select").selectOption("org");
  const orgMethods = await methods.count();
  check(
    "Sentry keeps both connection methods under Organization",
    personalMethods === 2 && orgMethods === 2,
    `${personalMethods} -> ${orgMethods}`,
  );
  await shot(modal, "after-sentry-organization-methods");
  form = await openOrgForm(page, modal);
  const sentryType = await value("org-server-type-dropdown");
  check(
    "Sentry: the chosen stdio method survives the scope switch",
    /stdio/i.test(sentryType),
    sentryType,
  );
  const sentryEnv = await value("org-env-input");
  check(
    "Sentry: the optional SENTRY_HOST is left out",
    sentryEnv === "SENTRY_ACCESS_TOKEN=${SENTRY_ACCESS_TOKEN}",
    sentryEnv,
  );
  await form.getByTestId("org-cancel-button").click();
  await form.waitFor({ state: "detached" });

  // GitHub is remote: its hand-off is unchanged.
  modal = await openEntry(page, "github");
  const mcpTab = modal.getByTestId("mcp-install-tab-mcp");
  if ((await mcpTab.count()) > 0) await mcpTab.click();
  await modal.getByTestId("mcp-install-scope-select").selectOption("org");
  form = await openOrgForm(page, modal);
  check(
    "GitHub still prefills its name and URL",
    (await value("org-name-input")) === "github" &&
      (await value("org-url-input")) === "https://api.githubcopilot.com/mcp/",
  );
  check(
    "GitHub's form has no command field",
    (await form.getByTestId("org-command-input").count()) === 0,
  );
  await form.getByTestId("org-cancel-button").click();

  for (const [org, who] of [
    [3, "A member"],
    [1, "A personal workspace"],
  ]) {
    await openSettings(page, org);
    modal = await openEntry(page, "tavily");
    await modal.getByTestId("mcp-install-field-TAVILY_API_KEY").waitFor();
    check(
      `${who} gets no Scope select`,
      (await modal.getByTestId("mcp-install-scope-select").count()) === 0,
    );
  }

  await browser.close();
  const failed = results.filter((ok) => !ok).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(2);
});
