import { chromium } from "playwright";

const FRONTEND = "http://127.0.0.1:7510";
const MATERIAL =
  "Mitochondria are the powerhouse of the cell, producing ATP through cellular " +
  "respiration. Chloroplasts conduct photosynthesis, converting light energy into " +
  "glucose. Ribosomes synthesize proteins from mRNA. The nucleus stores the cell's DNA.";

const browser = await chromium.launch();
const context = await browser.newContext({ permissions: [] });
const page = await context.newPage();

const failures = [];
page.on("console", (msg) => {
  if (msg.type() === "error") failures.push(`console error: ${msg.text()}`);
});
page.on("pageerror", (err) => failures.push(`page error: ${err.message}`));

const requests = [];
page.on("request", (req) => {
  if (req.url().includes(":8010")) requests.push(`${req.method()} ${new URL(req.url()).pathname}`);
});

console.log("=== 1. Load the app ===");
await page.goto(FRONTEND, { waitUntil: "networkidle" });
console.log("   title:", await page.title());
console.log("   tabs:", await page.locator("nav button").allInnerTexts());
await page.screenshot({ path: "/tmp/shot-1-upload.png", fullPage: true });

console.log("\n=== 2. Agent URL derived from window.location (7510 -> 8010) ===");
const derived = await page.evaluate(() => {
  const { protocol, hostname, port } = window.location;
  return `${protocol}//${hostname}:${Number(port) + 500}`;
});
console.log("   derived:", derived);
if (!derived.endsWith(":8010")) throw new Error(`expected :8010, got ${derived}`);

console.log("\n=== 3. Create a deck from pasted text (real Nova) ===");
await page.fill("#deck-title", "Cell Biology");
await page.fill("#deck-text", MATERIAL);
await page.click('button[type="submit"]');
// Card generation takes several seconds against the real model.
await page.waitForSelector("text=/Card 1 of/", { timeout: 180000 });
console.log("   deck created; switched to Study automatically");
const cardCount = await page.locator("text=/Card 1 of/").innerText();
console.log("   ", cardCount);
await page.screenshot({ path: "/tmp/shot-2-study.png", fullPage: true });

console.log("\n=== 4. Mic button present (voice affordance) ===");
const micVisible = await page.locator('button[aria-label="Record your answer"]').isVisible();
console.log("   mic button visible:", micVisible);

console.log("\n=== 5. Answer a card correctly ===");
const question = await page.locator("p.text-lg").innerText();
console.log("   Q:", question);
await page.fill("#answer", "It stores the cell's DNA and genetic material");
await page.click('button:has-text("Check my answer")');
await page.waitForSelector("text=/Correct|Not quite/", { timeout: 120000 });
const verdict = await page.locator("text=/Correct|Not quite/").first().innerText();
const explanation = await page.locator("p.text-sm.leading-relaxed").first().innerText();
const nextReview = await page.locator("text=/Next review/").innerText();
console.log("   verdict:", verdict);
console.log("   explanation:", explanation.slice(0, 100));
console.log("   ", nextReview);
await page.screenshot({ path: "/tmp/shot-3-graded.png", fullPage: true });

console.log("\n=== 6. Advance to the next card ===");
await page.click('button:has-text("Next card")');
await page.waitForSelector("text=/Card 2 of/", { timeout: 30000 });
console.log("   advanced to card 2");

console.log("\n=== 7. Progress screen shows real numbers ===");
await page.click('nav button:has-text("Progress")');
await page.waitForSelector("text=/Cards reviewed/", { timeout: 60000 });
const tiles = await page.locator("dl div").allInnerTexts();
console.log("   tiles:", tiles.map((t) => t.replace(/\n/g, "=")).join(" | "));
const weak = await page.locator("ul li").allInnerTexts().catch(() => []);
console.log("   weak topics:", weak.length ? weak.map((w) => w.split("\n")[0]).join(", ") : "(none yet)");
await page.screenshot({ path: "/tmp/shot-4-progress.png", fullPage: true });

console.log("\n=== 8. Memory persists across a reload ===");
await page.reload({ waitUntil: "networkidle" });
await page.click('nav button:has-text("Progress")');
await page.waitForSelector("text=/Cards reviewed/", { timeout: 60000 });
const afterReload = await page.locator("dl div").first().innerText();
console.log("   after reload:", afterReload.replace(/\n/g, "="));

console.log("\n=== Requests the browser made to the agent ===");
console.log("   " + [...new Set(requests)].join("\n   "));

console.log("\n=== JS errors ===");
console.log(failures.length ? failures.join("\n") : "   none");

await browser.close();
if (failures.length) process.exit(1);
console.log("\nSMOKE TEST PASSED");
