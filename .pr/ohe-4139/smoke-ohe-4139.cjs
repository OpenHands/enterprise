// Smoke test for OHE-4139 against the mock API: saving on Settings > MCP keeps
// the server settings agent-canvas stores (on/off, description, icon, working
// directory, timeouts), and each personal server has an on/off switch.
//
//   cd frontend
//   VITE_MOCK_ORG_MCP_SERVERS=true VITE_FRONTEND_PORT=3039 npm run dev:mock:saas
//   node ../.pr/ohe-4139/smoke-ohe-4139.cjs
//
// BEFORE=1 shows the loss and takes the "before" screenshot (run it on a
// checkout without the fix). BASE_URL overrides http://localhost:3039.
//
// The mock API keeps settings in the page's memory and, like the backend,
// replaces the stored MCP server list with the one a save sends. The servers
// are stored through the API, the way agent-canvas saves them, just before the
// page first reads its settings, so the page is the only thing under test.
const path = require("path");
const { chromium } = require("../../frontend/node_modules/@playwright/test");

const BASE_URL = process.env.BASE_URL ?? "http://localhost:3039";
const BEFORE = process.env.BEFORE === "1";

// Three switched-off servers, one per transport, and one that is on.
const CANVAS_SERVERS = {
  docs: {
    url: "https://docs.example.com/mcp",
    description: "Product docs",
    icon: "https://docs.example.com/icon.png",
    enabled: false,
  },
  events: {
    url: "https://events.example.com/sse",
    transport: "sse",
    description: "Event stream",
    icon: "https://events.example.com/icon.png",
    timeout: 45,
    enabled: false,
  },
  files: {
    command: "npx",
    args: ["-y", "fs-server"],
    cwd: "/workspace",
    description: "Workspace files",
    icon: "folder",
    timeout: 20,
    enabled: false,
  },
  search: { url: "https://search.example.com/mcp" },
};
const SWITCHED_OFF = ["docs", "events", "files"];

const results = [];
function check(name, ok, detail = "") {
  results.push(ok);
  const suffix = detail ? `  [${detail}]` : "";
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${suffix}`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const same = (a, b) => JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
function sorted(value) {
  if (Array.isArray(value)) return value.map(sorted);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sorted(value[key])]),
    );
  }
  return value;
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  const page = await browser.newPage({
    viewport: { width: 1280, height: 720 },
  });
  // Only the personal server list: endpoints the mock does not handle leave
  // unrelated error toasts in the corner of the page.
  const shot = (name) =>
    page
      .getByTestId("mcp-server-list")
      .screenshot({ path: path.join(__dirname, `${name}.png`) });

  const storedServers = () =>
    page.evaluate(async () => {
      const response = await fetch("/api/v1/settings");
      return (await response.json()).agent_settings.mcp_config;
    });
  // Poll the store until a save has landed.
  async function storedWhen(predicate) {
    let servers = await storedServers();
    for (let i = 0; i < 60 && !predicate(servers); i += 1) {
      await sleep(250);
      servers = await storedServers();
    }
    return servers;
  }
  const row = (name) =>
    page.getByTestId("mcp-server-item").filter({
      has: page.getByTitle(name, { exact: true }),
    });
  const editorIsClosed = async () =>
    (await page.getByTestId("edit-mcp-server-form").count()) === 0;
  const switchIsOn = (label) =>
    page.getByRole("switch", { name: label }).getAttribute("aria-checked");
  // The list redraws from a refetch after the save, so wait for it.
  const switchShows = (label, checked) =>
    page
      .getByRole("switch", { name: label, checked })
      .waitFor({ timeout: 10000 })
      .then(
        () => true,
        () => false,
      );

  // Store the servers the way agent-canvas does. A reload resets the mock's
  // settings, so this runs on every load, holding back the page's first
  // settings read until the servers are stored.
  await page.addInitScript((servers) => {
    const { open, send } = XMLHttpRequest.prototype;
    let stored;
    XMLHttpRequest.prototype.open = function patchedOpen(method, url, ...rest) {
      this.smokeRequest = `${method} ${url}`;
      return open.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function patchedSend(body) {
      if (!/^GET \S*\/api\/v1\/settings$/.test(this.smokeRequest)) {
        send.call(this, body);
        return;
      }
      stored ??= fetch("/api/v1/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_settings_diff: { mcp_config: servers } }),
      });
      stored.then(() => send.call(this, body));
    };
  }, CANVAS_SERVERS);

  // On a cold start Vite optimizes dependencies during the first request and
  // reloads the page, so load it once and let that settle.
  await page
    .goto(`${BASE_URL}/settings/mcp`, { waitUntil: "domcontentloaded" })
    .catch(() => {});
  await sleep(8000);
  await page.goto(`${BASE_URL}/settings/mcp`);
  await row("docs").waitFor({ timeout: 60000 });

  const addServer = async () => {
    await page.getByRole("button", { name: "Add Server", exact: true }).click();
    await page.getByTestId("name-input").fill("smoke-new");
    await page.getByTestId("url-input").fill("https://smoke.example.com/sse");
    await page.getByTestId("submit-button").click();
    await row("smoke-new").waitFor();
    return storedWhen((servers) => "smoke-new" in servers);
  };

  if (BEFORE) {
    // Without the fix the page has no switch, and the three switched-off
    // servers look like any other server.
    await shot("before-switched-off-servers-look-on");
    const stored = await addServer();
    for (const name of SWITCHED_OFF) {
      const lost = Object.keys(CANVAS_SERVERS[name]).filter(
        (key) => !(key in stored[name]),
      );
      console.log(`LOST  ${name}: ${lost.join(", ") || "nothing"}`);
    }
    await browser.close();
    return;
  }

  // 1. The page shows what is stored.
  const shown = {};
  for (const label of ["Docs", "Events", "Files", "Search"]) {
    shown[label] = await switchIsOn(`Enable ${label}`);
  }
  check(
    "the three switched-off servers show as off and Search as on",
    same(shown, {
      Docs: "false",
      Events: "false",
      Files: "false",
      Search: "true",
    }),
    JSON.stringify(shown),
  );
  await shot("after-switched-off-servers");

  // 2. Adding a different server.
  let stored = await addServer();
  for (const name of SWITCHED_OFF) {
    check(
      `adding a server leaves ${name} off with all of its settings`,
      same(stored[name], CANVAS_SERVERS[name]),
      JSON.stringify(stored[name]),
    );
  }
  await shot("after-adding-a-server");

  // 3. Editing a server keeps the settings its form has no field for.
  await row("files").getByTestId("edit-mcp-server-button").click();
  await page.getByTestId("args-input").fill("-y\nfs-server\n--read-only");
  await page.getByTestId("submit-button").click();
  stored = await storedWhen((servers) => servers.files.args?.length === 3);
  const { args: filesArgs, env: filesEnv, ...filesRest } = stored.files;
  const { args: seededArgs, ...seededFilesRest } = CANVAS_SERVERS.files;
  check(
    "editing the stdio server keeps its working directory, timeout, description, icon and off state",
    filesArgs.length === 3 && same(filesRest, seededFilesRest),
    JSON.stringify(stored.files),
  );

  await row("events").getByTestId("edit-mcp-server-button").click();
  await page.getByTestId("url-input").fill("https://events.example.com/v2/sse");
  await page.getByTestId("submit-button").click();
  stored = await storedWhen((servers) => servers.events.url.includes("/v2/"));
  check(
    "editing the SSE server keeps its timeout, description, icon and off state",
    same(stored.events, {
      ...CANVAS_SERVERS.events,
      url: "https://events.example.com/v2/sse",
    }),
    JSON.stringify(stored.events),
  );

  // 4. Deleting a server.
  await row("smoke-new").getByTestId("delete-mcp-server-button").click();
  await page.getByTestId("confirm-button").click();
  stored = await storedWhen((servers) => !("smoke-new" in servers));
  check(
    "deleting a server leaves docs off with all of its settings",
    same(stored.docs, CANVAS_SERVERS.docs),
    JSON.stringify(stored.docs),
  );

  // 5. The switch, by mouse and by keyboard. Neither opens the editor.
  await page.getByRole("switch", { name: "Enable Search" }).click();
  stored = await storedWhen((servers) => servers.search.enabled === false);
  check(
    "clicking the switch turns Search off",
    stored.search.enabled === false &&
      (await switchShows("Enable Search", false)) &&
      (await editorIsClosed()),
    JSON.stringify(stored.search),
  );

  await page.getByRole("switch", { name: "Enable Docs" }).focus();
  await page.keyboard.press("Space");
  stored = await storedWhen((servers) => servers.docs.enabled === true);
  check(
    "Space on the switch turns Docs on and keeps its description and icon",
    same(stored.docs, { ...CANVAS_SERVERS.docs, enabled: true }) &&
      (await switchShows("Enable Docs", true)) &&
      (await editorIsClosed()),
    JSON.stringify(stored.docs),
  );
  await sleep(500); // let the switch finish its transition
  await shot("after-using-the-switch");

  await browser.close();
  const failed = results.filter((ok) => !ok).length;
  console.log(`${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
