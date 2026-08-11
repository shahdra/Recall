import { chromium } from "playwright";

// Defaults to the docker-compose stack (frontend 3000 -> agent 3500). Override
// for the host-process setup from scripts/start-local.sh:
//   APP_URL=http://127.0.0.1:7510 node smoke.mjs
const FRONTEND = process.env.APP_URL ?? "http://127.0.0.1:3000";
// The +500 offset is the rule under test (lib/api.ts), so it is computed here
// rather than hardcoded — a wrong offset must fail, not be asserted into.
const AGENT_PORT = Number(new URL(FRONTEND).port) + 500;
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
  if (req.url().includes(`:${AGENT_PORT}`)) {
    requests.push(`${req.method()} ${new URL(req.url()).pathname}`);
  }
});

console.log("=== 1. Load the app ===");
await page.goto(FRONTEND, { waitUntil: "networkidle" });
console.log("   title:", await page.title());
console.log("   tabs:", await page.locator("nav button").allInnerTexts());
await page.screenshot({ path: "/tmp/shot-1-upload.png", fullPage: true });

console.log(`\n=== 2. Agent URL derived from window.location (-> ${AGENT_PORT}) ===`);
const derived = await page.evaluate(() => {
  const { protocol, hostname, port } = window.location;
  return `${protocol}//${hostname}:${Number(port) + 500}`;
});
console.log("   derived:", derived);
if (!derived.endsWith(`:${AGENT_PORT}`)) {
  throw new Error(`expected :${AGENT_PORT}, got ${derived}`);
}

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
// The deck order is whatever the model generated, so a hardcoded answer only
// matches by luck — it previously drew the ribosomes card, answered about the
// nucleus, and the Grader correctly said "Not quite", which read as a failure of
// the app rather than of the harness. Answer from the material by topic instead.
const ANSWERS = [
  [/ribosome/i, "Ribosomes synthesize proteins from mRNA"],
  [/mitochondri|atp|respiration/i, "Mitochondria produce ATP through cellular respiration"],
  [/chloroplast|photosynthesis/i, "Chloroplasts conduct photosynthesis, turning light into glucose"],
  [/nucleus|dna|genetic/i, "The nucleus stores the cell's DNA"],
];
const answer =
  ANSWERS.find(([pattern]) => pattern.test(question))?.[1] ??
  "Ribosomes synthesize proteins from mRNA";
console.log("   answering:", answer);
await page.fill("#answer", answer);
await page.click('button:has-text("Check my answer")');
await page.waitForSelector("text=/Correct|Not quite/", { timeout: 120000 });
const verdict = await page.locator("text=/Correct|Not quite/").first().innerText();
const explanation = await page.locator("p.text-sm.leading-relaxed").first().innerText();
const nextReview = await page.locator("text=/Next review/").innerText();
console.log("   verdict:", verdict);
// Assert it, so a mismatched answer shows up as a harness failure rather than
// printing "Not quite" under a step titled "answer a card correctly".
if (!/Correct/i.test(verdict)) {
  throw new Error(`expected a correct verdict for "${question}", got: ${verdict}`);
}
console.log("   explanation:", explanation.slice(0, 100));
console.log("   ", nextReview);
await page.screenshot({ path: "/tmp/shot-3-graded.png", fullPage: true });

console.log("\n=== 6. Advance to the next card ===");
await page.click('button:has-text("Next card")');
await page.waitForSelector("text=/Card 2 of/", { timeout: 30000 });
console.log("   advanced to card 2");

console.log("\n=== 6b. A second deck brings up the deck selector ===");
// The whole point of the selector is telling two decks apart, which no unit test
// can reach — it needs two real decks and a real browser.
await page.click('nav button:has-text("Add material")');
await page.waitForSelector("#deck-text", { timeout: 30000 });
await page.fill("#deck-title", "Kubernetes");
await page.fill("#deck-text", "teach me Kubernetes basics");
await page.click('button[type="submit"]');
await page.waitForSelector("#deck-filter", { timeout: 180000 });
const options = await page.locator("#deck-filter option").allInnerTexts();
console.log("   options:", options.join(" | "));
if (!options.some((o) => /^All decks/.test(o))) {
  throw new Error(`expected an "All decks" option, got: ${options.join(" | ")}`);
}
for (const title of ["Cell Biology", "Kubernetes"]) {
  if (!options.some((o) => o.includes(title))) {
    throw new Error(`expected ${title} in the selector, got: ${options.join(" | ")}`);
  }
}
// Default must stay interleaved so mixed practice is what you get by choosing
// nothing.
const defaultDeck = await page.locator("#deck-filter").inputValue();
if (defaultDeck !== "__all__") {
  throw new Error(`expected the interleaved default, got "${defaultDeck}"`);
}
// On the mixed queue every card says which deck it came from.
const deckChip = await page.locator("span.border.truncate, span.max-w-\\[12rem\\]").first().innerText();
console.log("   deck label on card:", deckChip);
if (!/Cell Biology|Kubernetes/.test(deckChip)) {
  throw new Error(`expected a deck name on the card, got "${deckChip}"`);
}
await page.screenshot({ path: "/tmp/shot-5-selector.png", fullPage: true });

console.log("\n=== 6c. Selecting one deck narrows the queue to it ===");
const kubeOption = options.find((o) => o.includes("Kubernetes"));
const kubeDue = Number(kubeOption.match(/\((\d+) due\)/)?.[1]);
const allDue = Number(options.find((o) => /^All decks/.test(o)).match(/\((\d+) due\)/)?.[1]);
console.log(`   all=${allDue} kubernetes=${kubeDue}`);
if (!(kubeDue > 0 && kubeDue < allDue)) {
  throw new Error(`expected a Kubernetes subset of the queue, got ${kubeDue}/${allDue}`);
}
await page.selectOption("#deck-filter", { label: kubeOption });
await page.waitForSelector(`text=/Card 1 of ${kubeDue}/`, { timeout: 30000 });
console.log(`   queue narrowed to ${kubeDue} cards`);
// With one deck selected the label is redundant, so it should be gone.
const chipsNow = await page.locator("span.max-w-\\[12rem\\]").count();
console.log("   deck label hidden while filtered:", chipsNow === 0);

console.log("\n=== 6d. Finishing a deck advances to the next one ===");
// Answering a real 11-card deck through the UI would cost ~11 Bedrock grader
// calls, so the auto-advance is tested on two deliberately tiny decks under a
// fresh user id. The behaviour under test is the client's, but the decks and
// grading are real.
const AGENT = `http://127.0.0.1:${AGENT_PORT}`;
const advanceUser = `smoke-advance-${Date.now()}`;
const post = async (path, body) => {
  const res = await fetch(`${AGENT}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${await res.text()}`);
  return res.json();
};
// One sentence each, so the generator produces a very small deck.
const small = await post("/decks", {
  user_id: advanceUser,
  title: "Tiny A",
  text: "The mitochondrion produces ATP.",
});
const second = await post("/decks", {
  user_id: advanceUser,
  title: "Tiny B",
  text: "The ribosome builds proteins from mRNA.",
});
console.log(`   Tiny A: ${small.card_count} cards, Tiny B: ${second.card_count} cards`);
if (!small.card_count || !second.card_count) {
  throw new Error("both decks need cards to test advancing between them");
}

// Study as that user by overriding the id the app stores, then reload. The
// original is put back afterwards — clearing the key would mint a new random
// learner and the progress checks below would find an empty history.
const originalUser = await page.evaluate(() =>
  window.localStorage.getItem("recall.user_id"),
);
await page.evaluate((id) => window.localStorage.setItem("recall.user_id", id), advanceUser);
await page.reload({ waitUntil: "networkidle" });
await page.click('nav button:has-text("Study")');
await page.waitForSelector("#deck-filter", { timeout: 60000 });
const twoOptions = await page.locator("#deck-filter option").allInnerTexts();
console.log("   options:", twoOptions.join(" | "));

// Pick the smaller deck so it finishes in the fewest grader calls.
const counts = twoOptions
  .filter((o) => !/^All decks/.test(o))
  .map((o) => ({ label: o, n: Number(o.match(/\((\d+) due\)/)?.[1] ?? 0) }))
  .sort((a, b) => a.n - b.n);
const target = counts[0];
const other = counts[counts.length - 1];
console.log(`   studying ${target.label} (finishing it should jump to the other deck)`);
await page.selectOption("#deck-filter", { label: target.label });

for (let i = 0; i < target.n; i += 1) {
  await page.fill("#answer", "I don't remember");
  await page.click('button:has-text("Check my answer")');
  await page.waitForSelector("text=/Correct|Not quite/", { timeout: 120000 });
  await page.click('button:has-text("Next card"), button:has-text("Next deck"), button:has-text("Finish session")');
}

// Studying should continue in the other deck rather than ending. With only one
// deck left there is no longer a choice to make, so the dropdown is expected to
// be GONE here — asserting on it would be asserting the wrong thing.
const remainingCount = other.n;
await page.waitForSelector(`text=/Card 1 of ${remainingCount}/`, { timeout: 30000 });
console.log(`   continued into the remaining deck (${remainingCount} cards)`);
if (await page.locator("text=/Nothing due right now/").isVisible().catch(() => false)) {
  throw new Error("session ended instead of advancing to the remaining deck");
}
if (await page.locator("#deck-filter").isVisible().catch(() => false)) {
  throw new Error("selector should be hidden once only one deck has cards left");
}
// The toast is the only signal that the deck changed under you, so it matters.
const movedOn = await page
  .locator("text=/Deck finished/")
  .first()
  .innerText()
  .catch(() => "");
console.log("   toast:", movedOn || "(gone before it could be read)");
console.log("   auto-advance works; selector correctly hidden with one deck left");
await page.screenshot({ path: "/tmp/shot-6-advanced.png", fullPage: true });

// Back to the original learner for the progress checks below.
await page.evaluate((id) => {
  if (id) window.localStorage.setItem("recall.user_id", id);
}, originalUser);
await page.reload({ waitUntil: "networkidle" });

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
