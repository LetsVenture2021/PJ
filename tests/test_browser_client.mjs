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
});

test("mobile, keyboard approval and reduced-motion accessibility hooks exist", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  assert.match(html, /@media \(max-width: 640px\)/);
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /class="skip-link"/);
  assert.match(html, /role="log"/);
  assert.match(html, /approval-actions/);
});
