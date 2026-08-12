/**
 * Verify the browser -> Deepgram voice path end to end.
 *
 * This settles a gap the Python tests could not: the tutor-agent's live tests
 * confirmed WAV and M4A, but Chrome and Firefox record WebM/Opus, which needs a
 * real browser to produce. Playwright's fake audio device feeds getUserMedia,
 * MediaRecorder encodes to WebM/Opus, and the result goes through the real
 * /transcribe endpoint.
 *
 * What this proves is the *format* path — that Deepgram accepts browser-recorded
 * WebM/Opus with no format hint. It does not check transcript accuracy:
 * Chromium's fake audio device does not reliably play the supplied WAV, so the
 * returned text is whatever its synthetic tone decodes to. Accuracy is covered
 * by services/tutor-agent/tests/integration/test_voice_live.py, which uses real
 * synthesized speech.
 *
 *   node voice-check.mjs            # needs the stack from scripts/start-local.sh
 */
import { chromium } from "playwright";

const AGENT = process.env.AGENT_URL ?? "http://127.0.0.1:8010";
const WAV = process.env.VOICE_WAV ?? "/tmp/recall_test.wav";
// getUserMedia needs a secure context, so run inside the served app rather than
// about:blank — on about:blank navigator.mediaDevices is undefined.
const APP = process.env.APP_URL ?? "http://localhost:7510";

const browser = await chromium.launch({
  args: [
    "--use-fake-device-for-media-capture",
    "--use-fake-ui-for-media-stream",
    `--use-file-for-fake-audio-capture=${WAV}`,
  ],
});
const context = await browser.newContext({ permissions: ["microphone"] });
const page = await context.newPage();
await page.goto(APP, { waitUntil: "domcontentloaded" });

console.log("=== What can this browser record? ===");
const supported = await page.evaluate(() =>
  [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ].filter((type) => MediaRecorder.isTypeSupported(type)),
);
console.log("   supported:", supported.join(", ") || "(none)");

console.log("\n=== Record from the fake mic, then transcribe ===");
const result = await page.evaluate(async (agent) => {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  const mimeType = candidates.find((t) => MediaRecorder.isTypeSupported(t)) ?? "";
  const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);

  const chunks = [];
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };

  const stopped = new Promise((resolve) => {
    recorder.onstop = () => resolve();
  });
  recorder.start();
  await new Promise((resolve) => setTimeout(resolve, 4000));
  recorder.stop();
  await stopped;
  stream.getTracks().forEach((track) => track.stop());

  const blob = new Blob(chunks, { type: recorder.mimeType });
  const base64 = await new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.readAsDataURL(blob);
  });

  const response = await fetch(`${agent}/transcribe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ audio_b64: base64 }),
  });
  const body = await response.json();
  return { recordedAs: recorder.mimeType, bytes: blob.size, status: response.status, body };
}, AGENT);

console.log("   recorded as:", result.recordedAs);
console.log("   audio bytes:", result.bytes);
console.log("   HTTP:", result.status);
console.log("   transcript:", JSON.stringify(result.body));

await browser.close();

if (!result.recordedAs.includes("webm")) {
  console.log("\nNote: this browser did not produce WebM, so Opus is still unverified.");
}
if (!result.body.text) {
  console.error("\nFAILED: no transcript returned");
  process.exit(1);
}
console.log("\nVOICE PATH VERIFIED (browser recording -> Deepgram -> text)");
