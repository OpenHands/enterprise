// In frontend: VITE_MOCK_API=true VITE_MOCK_SAAS=true react-router dev --port 3197
// From frontend: PROOF_OUTPUT=/tmp/settings-proof node ../.pr/reproduce-settings-loading.mjs before|after
import { chromium } from "../frontend/node_modules/@playwright/test/index.mjs";
import { mkdir, writeFile } from "node:fs/promises";
const mode = process.argv[2] ?? "after";
const output = process.env.PROOF_OUTPUT ?? "/tmp/settings-proof";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  recordVideo: { dir: output, size: { width: 1440, height: 900 } },
});
const page = await context.newPage();
await page.addInitScript(() => {
  window.spinnerSamples = [];
  setInterval(() => {
    const rows = [
      ...document.querySelectorAll('[data-testid="loading-spinner"]'),
    ]
      .map((e) => {
        const r = e.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
      })
      .filter((r) => r.w >= 40);
    const key = JSON.stringify(rows);
    if (key !== window.lastSpinner) {
      window.lastSpinner = key;
      window.spinnerSamples.push({ at: performance.now(), rows });
    }
  }, 16);
});
await page.route("**/src/entry.client.tsx", async (route) => {
  const response = await route.fetch();
  const body = await response.text();
  const delays = `const {worker:proofWorker}=await import('/src/mocks/browser.ts');
 const {http:proofHttp,delay:proofDelay}=await import('/node_modules/.vite/deps/msw.js');
 proofWorker.use(proofHttp.all('*',async ({request})=>{
 const pathname=new URL(request.url).pathname;
 if(pathname==='/api/authenticate')await proofDelay(1400);
 if(/profiles|provider-connections/.test(pathname))await proofDelay(2300);
 }));\n`;
  await route.fulfill({ response, body: delays + body });
});
try {
  await page.goto("http://127.0.0.1:3197/settings", {
    waitUntil: "domcontentloaded",
  });
  await page
    .getByText("Available Profiles", { exact: true })
    .waitFor({ timeout: 60000 });
  await page.waitForTimeout(350);
  const panelSpinners = await page
    .getByTestId("loading-spinner")
    .filter({ visible: true })
    .count();
  await page.screenshot({ path: `${output}/${mode}.png` });
  if (mode === "after" ? panelSpinners !== 0 : panelSpinners < 1)
    throw new Error(`Unexpected panel spinner count: ${panelSpinners}`);
  await page.getByTestId("profile-row").first().waitFor();
  await page.waitForTimeout(1200);
  const samples = await page.evaluate(() => window.spinnerSamples);
  console.log(JSON.stringify({ mode, panelSpinners, samples }));
  await writeFile(
    `${output}/${mode}-samples.json`,
    JSON.stringify(samples, null, 2),
  );
} catch (error) {
  await page.screenshot({ path: `${output}/${mode}-error.png` });
  console.log((await page.locator("body").innerText()).slice(0, 1000));
  throw error;
} finally {
  await context.close();
  console.log(JSON.stringify({ video: await page.video().path() }));
  await browser.close();
}
