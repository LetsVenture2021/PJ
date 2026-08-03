import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { SessionController, backoffDelay } from "../web/session_controller.js";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
}

test("session persistence excludes private content", () => {
  const target = storage();
  const controller = new SessionController(target);
  controller.persist({
    conversationId: "conversation_12345678",
    eventCursor: "cursor-4",
    preferences: { reducedMotion: true },
    uploads: [{ uploadId: `UPL-${"a".repeat(32)}`, offset: 12, size: 30 }],
    prompt: "private",
    toolArguments: { secret: true },
  });
  assert.deepEqual(Object.keys(controller.load()).sort(), [
    "conversationId",
    "eventCursor",
    "preferences",
    "uploads",
  ]);
  assert.doesNotMatch(JSON.stringify(controller.load()), /private|secret/);
});

test("duplicate submissions share one in-flight operation", async () => {
  const controller = new SessionController(storage());
  let calls = 0;
  const operation = async () => {
    calls += 1;
    return "ok";
  };
  assert.deepEqual(
    await Promise.all([
      controller.submit("same-key", operation),
      controller.submit("same-key", operation),
    ]),
    ["ok", "ok"],
  );
  assert.equal(calls, 1);
});

test("offline retry is bounded exponential backoff", () => {
  assert.equal(
    backoffDelay(0, () => 0),
    375,
  );
  assert.equal(
    backoffDelay(99, () => 1),
    30_000,
  );
});

test("service worker uses a static allowlist and excludes private routes", async () => {
  const source = await readFile(new URL("../web/service-worker.js", import.meta.url), "utf8");
  assert.match(source, /STATIC\.includes\(url\.pathname\)/);
  const allowlist = source.match(/const STATIC = \[([\s\S]*?)\];/)[1];
  for (const privatePath of ["upload/files", "responses/", "artifacts", "transcript"]) {
    assert.doesNotMatch(allowlist, new RegExp(privatePath, "i"));
  }
  assert.match(source, /fetch\(event\.request\)[\s\S]*\.catch\(\(\) => caches\.match/);
});

test("voice signaling falls back before validating a non-PJ origin error", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  assert.match(html, /status === 404 \|\| status === 405/);
  const errorBranch = html.indexOf("if (!response.ok)", html.indexOf("const signalingUrl"));
  const protocolCheck = html.indexOf("assertProtocolResponse(response);", errorBranch);
  assert.ok(errorBranch > -1 && protocolCheck > errorBranch);
  assert.match(html.slice(errorBranch, protocolCheck), /shouldUseEphemeralSignalingFallback/);
});

test("legacy public health cannot block versioned voice session startup", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  const healthStart = html.indexOf("async function checkBackendHealth");
  const healthEnd = html.indexOf("function summarizeToolNames", healthStart);
  const healthSource = html.slice(healthStart, healthEnd);
  assert.match(healthSource, /if \(!parsed\.ok\)/);
  assert.match(healthSource, /healthVersion !== null && healthVersion !== undefined/);
  assert.match(healthSource, /assertProtocolResponse\(response, parsed\)/);

  const sessionStart = html.indexOf("async function ensureActiveSession");
  const sessionEnd = html.indexOf("async function loadSharedSessionHistory", sessionStart);
  assert.match(html.slice(sessionStart, sessionEnd), /fetchJson/);
});

test("mobile, keyboard approval and reduced-motion accessibility hooks exist", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  assert.match(html, /@media \(max-width: 640px\)/);
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /class="skip-link"/);
  assert.match(html, /role="log"/);
  assert.match(html, /approval-actions/);
});

test("voice modes, long prompts, and document artifacts are reachable from the UI", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  assert.match(html, /id="fastVoiceModeBtn"/);
  assert.match(html, /setMode\(MODE\.REALTIME\)/);
  assert.match(html, /id="textInput" maxlength="100000"/);
  assert.match(html, /Create a document/);
  assert.match(html, /downloadable artifact/);
});

test("artifact and feature-context controls have executable handlers", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  for (const action of [
    "preview",
    "compare",
    "restore-as-new",
    "follow-up",
    "minimize",
    "remove",
  ]) {
    assert.match(html, new RegExp(`action === ["']${action}["']`));
  }
  assert.match(html, /perfectFeatureInput/);
  assert.match(html, /id="addProjectSourcesBtn"/);
  assert.match(html, /id="addProjectArtifactsBtn"/);
  assert.match(html, /structuredOutputEnabled\.checked/);
  assert.match(html, /state\.activeSessionId \|\| state\.fullPowerSessionId/);
});

test("non-JSON response payloads surface actionable errors instead of raw parse failures", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  // A non-JSON body arriving with a JSON content-type (for example an edge
  // security interstitial) must not throw a raw SyntaxError. Every response
  // parse guards JSON.parse and re-throws buildApiFailureDetail(...).
  assert.doesNotMatch(html, /const parsed = body \? JSON\.parse\(body\)/);
  assert.match(html, /buildApiFailureDetail\(response, body, "Backend health response"\)/);
  assert.match(html, /buildApiFailureDetail\(response, body, "Tool schema response"\)/);
});
