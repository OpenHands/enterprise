/* Headless smoke test for OHE-3937 against the enterprise frontend mock API. */
const fs = require("fs");
const {
  chromium,
} = require("/Users/hieple/Documents/openhands/OpenHands/.claude/worktrees/ohe-3937/frontend/node_modules/@playwright/test");

const BASE = process.env.BASE || "http://localhost:3023";
const OUT =
  "/private/tmp/claude-501/-Users-hieple-Documents-openhands-OpenHands/4b4966c9-2e52-4a8a-a854-9267a323ccae/scratchpad/smoke";
fs.mkdirSync(OUT, { recursive: true });

const shot = (page, name) =>
  page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });

async function expectText(page, text, timeout = 10000) {
  await page.getByText(text, { exact: false }).first().waitFor({ timeout });
  console.log("ok: saw", JSON.stringify(text));
}

async function openAdd(page) {
  await page.getByRole("button", { name: "Add Server", exact: true }).click();
  await page.getByTestId("add-mcp-server-modal").waitFor();
}

async function pickType(page, label) {
  await page.getByTestId("server-type-dropdown").click();
  await page.getByRole("option", { name: label, exact: true }).click();
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 1000 },
  });

  // The MSW mock serves no /api/agent-profiles routes. Short-circuit them in
  // XHR (axios) so the rename path can show the profile repoint end to end.
  await context.addInitScript(() => {
    const profiles = {
      profiles: [
        {
          id: "reviewer-id",
          name: "reviewer",
          agent_kind: "openhands",
          revision: 3,
          llm_profile_ref: "default",
          mcp_server_refs: ["notion", "github"],
          scope: "personal",
        },
        {
          id: "team-id",
          name: "team",
          agent_kind: "openhands",
          revision: 1,
          llm_profile_ref: "default",
          mcp_server_refs: ["notion"],
          scope: "org",
        },
      ],
      active_agent_profile_id: null,
    };
    const detail = {
      name: "reviewer",
      scope: "personal",
      profile: {
        id: "reviewer-id",
        name: "reviewer",
        revision: 3,
        agent_kind: "openhands",
        llm_profile_ref: "default",
        mcp_server_refs: ["notion", "github"],
        persona: "Reviews code",
      },
    };
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this.__m = method;
      this.__u = String(url);
      return origOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function (body) {
      const u = new URL(this.__u, location.origin);
      let payload;
      if (u.pathname === "/api/agent-profiles" && this.__m === "GET") {
        payload = profiles;
      } else if (
        u.pathname === "/api/agent-profiles/reviewer" &&
        this.__m === "GET"
      ) {
        payload = detail;
      } else if (
        u.pathname === "/api/agent-profiles/reviewer" &&
        this.__m === "POST"
      ) {
        const saves = JSON.parse(sessionStorage.getItem("__profileSaves") || "[]");
        saves.push({ query: u.search, body: JSON.parse(body) });
        sessionStorage.setItem("__profileSaves", JSON.stringify(saves));
        payload = null;
      } else {
        return origSend.call(this, body);
      }
      const text = payload === null ? "" : JSON.stringify(payload);
      const define = (k, v) =>
        Object.defineProperty(this, k, { value: v, configurable: true });
      define("readyState", 4);
      define("status", 200);
      define("statusText", "OK");
      define("responseText", text);
      define("response", text);
      define("getAllResponseHeaders", () => "content-type: application/json\r\n");
      define("getResponseHeader", (h) =>
        h.toLowerCase() === "content-type" ? "application/json" : null,
      );
      setTimeout(() => {
        this.dispatchEvent(new Event("readystatechange"));
        this.dispatchEvent(new ProgressEvent("load"));
        this.dispatchEvent(new ProgressEvent("loadend"));
      }, 0);
      return undefined;
    };
  });

  const page = await context.newPage();
  page.on("pageerror", (e) => console.log("pageerror:", e.message));
  const failed = [];
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.request().method()} ${r.url()} -> ${r.status()}`);
  });

  // Vite optimizes deps on the first request and reloads; settle, then reload.
  await page.goto(`${BASE}/settings/mcp`, { waitUntil: "networkidle" });
  await page.waitForTimeout(8000);
  await page.reload({ waitUntil: "networkidle" });
  await page.getByTestId("mcp-settings-screen").waitFor({ timeout: 30000 });
  await page.getByTestId("mcp-server-item").first().waitFor();
  await shot(page, "01-list-initial");

  // 1. The add form shows an optional Name for a remote (SSE) server.
  await openAdd(page);
  const nameInput = page.getByTestId("add-mcp-server-modal").getByTestId("name-input");
  await nameInput.waitFor();
  console.log("ok: name-input rendered for SSE; required =", await nameInput.getAttribute("required"));
  await shot(page, "02-add-form-sse-optional-name");

  // 2. Add an SHTTP server with a chosen name.
  await pickType(page, "SHTTP");
  await page.getByTestId("name-input").fill("sonarqube");
  await page.getByTestId("url-input").fill("https://tools.example.com/sonarqube-mcp/mcp");
  await page.getByTestId("api-key-input").fill("${SONAR_TOKEN}");
  await shot(page, "03-add-form-shttp-filled");
  await page.getByTestId("submit-button").click();
  await page.getByTestId("add-mcp-server-modal").waitFor({ state: "hidden" });
  await expectText(page, "Sonarqube");
  await shot(page, "04-list-after-add");

  // 3. A STDIO server may not reuse a remote server's name (case-insensitive).
  await openAdd(page);
  await pickType(page, "STDIO");
  await page.getByTestId("name-input").fill("Sonarqube");
  await page.getByTestId("command-input").fill("npx");
  await page.getByTestId("submit-button").click();
  await expectText(page, "An MCP server with this name already exists");
  await shot(page, "05-duplicate-across-types-rejected");
  await page.getByTestId("cancel-button").click();

  // 4. A remote server may not take an organization listing's name.
  await openAdd(page);
  await pickType(page, "SHTTP");
  await page.getByTestId("name-input").fill("datadog-shared");
  await page.getByTestId("url-input").fill("https://other.example.com/mcp");
  await page.getByTestId("submit-button").click();
  await expectText(page, "Your organization already provides an MCP server with this name");
  await shot(page, "06-reserved-org-name-rejected");
  await page.getByTestId("cancel-button").click();

  // 5. Rename an existing remote server (keyed "notion") on edit.
  await page.getByTestId("mcp-server-item").filter({ hasText: "Notion" }).first().click();
  await page.getByTestId("edit-mcp-server-modal").waitFor();
  const editName = page.getByTestId("edit-mcp-server-modal").getByTestId("name-input");
  console.log("ok: edit form name prefilled with", JSON.stringify(await editName.inputValue()));
  await shot(page, "07-edit-form-prefilled");
  await editName.fill("notion-docs");
  await page.getByTestId("submit-button").click();
  await page.getByTestId("edit-mcp-server-modal").waitFor({ state: "hidden" });
  await expectText(page, "Notion Docs");
  await page.waitForTimeout(1000);
  await shot(page, "08-list-after-rename");
  const saves = await page.evaluate(() => sessionStorage.getItem("__profileSaves"));
  console.log("profile saves after rename:", saves);

  // 6. The old key is gone from the list (the mock replaces the catalog like
  // the backend); the mock's state is in-memory, so no reload check here.
  const titles = (await page.getByTestId("mcp-server-item").allInnerTexts()).map(
    (t) => t.split("\n")[0].trim(),
  );
  console.log("personal servers after rename:", JSON.stringify(titles));
  if (titles.includes("Notion") || !titles.includes("Notion Docs")) {
    throw new Error("rename did not replace the old key in the list");
  }
  console.log("failed responses seen:", JSON.stringify(failed));

  await browser.close();
  console.log("SMOKE OK");
})().catch((e) => {
  console.error("SMOKE FAILED:", e);
  process.exit(1);
});
