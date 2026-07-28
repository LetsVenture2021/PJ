import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workerSource = await readFile(new URL("../pj_realtime_backend_worker.js", import.meta.url), "utf8");
const workerModule = await import(
  `data:text/javascript;base64,${Buffer.from(workerSource).toString("base64")}`
);

const {
  bridgeHeaders,
  buildAccessConfig,
  default: worker,
  deriveResponsesBridgeBaseUrl,
  handleResponsesProxy,
  isPublicRoute,
  isResponsesRoute,
  normalizeFunctionTools,
  responseHeaders,
  safeAttachmentDisposition,
  toolBridgeTimeoutMs,
  validateAccessIdentity,
} = workerModule;

const TEAM_DOMAIN = "pj-owner-tests.cloudflareaccess.com";
const ISSUER = `https://${TEAM_DOMAIN}`;
const AUDIENCE = "test-access-audience";
const OWNER_EMAIL = "owner@example.com";
const NOW_SECONDS = Math.floor(Date.now() / 1000);

const keyPair = await crypto.subtle.generateKey(
  {
    name: "RSASSA-PKCS1-v1_5",
    modulusLength: 2048,
    publicExponent: new Uint8Array([1, 0, 1]),
    hash: "SHA-256",
  },
  true,
  ["sign", "verify"],
);
const publicJwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
publicJwk.kid = "owner-test-key";
publicJwk.alg = "RS256";
publicJwk.use = "sig";

const env = {
  CF_ACCESS_TEAM_DOMAIN: TEAM_DOMAIN,
  CF_ACCESS_AUD: AUDIENCE,
  PJ_OWNER_EMAILS: OWNER_EMAIL,
  PJ_REALTIME_TOOL_SCHEMAS_JSON: "[]",
};

function base64Url(value) {
  const bytes = typeof value === "string" ? Buffer.from(value) : Buffer.from(value);
  return bytes.toString("base64url");
}

async function signAssertion(overrides = {}) {
  const header = { alg: "RS256", kid: publicJwk.kid, typ: "JWT" };
  const claims = {
    iss: ISSUER,
    aud: AUDIENCE,
    email: OWNER_EMAIL,
    sub: "owner-subject",
    iat: NOW_SECONDS,
    exp: NOW_SECONDS + 300,
    ...overrides,
  };
  const signingInput = `${base64Url(JSON.stringify(header))}.${base64Url(JSON.stringify(claims))}`;
  const signature = await crypto.subtle.sign(
    "RSASSA-PKCS1-v1_5",
    keyPair.privateKey,
    Buffer.from(signingInput),
  );
  return `${signingInput}.${base64Url(signature)}`;
}

async function certFetch() {
  return new Response(JSON.stringify({ keys: [publicJwk] }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function assertionRequest(assertion) {
  return new Request("https://pj-assistant.ai/tool-schemas", {
    headers: assertion ? { "Cf-Access-Jwt-Assertion": assertion } : {},
  });
}

test("only health and preflight are public", () => {
  assert.equal(isPublicRoute("GET", "/health"), true);
  assert.equal(isPublicRoute("OPTIONS", "/future-full-power"), true);
  for (const [method, path] of [
    ["POST", "/session"],
    ["POST", "/token"],
    ["GET", "/tool-schemas"],
    ["POST", "/execute-tool"],
    ["GET", "/responses/capabilities"],
    ["POST", "/responses/sessions/example_123/turns"],
    ["POST", "/future-full-power"],
  ]) {
    assert.equal(isPublicRoute(method, path), false, `${method} ${path} must be privileged`);
  }
});

test("Full Power routes and bridge URL derivation are narrowly scoped", () => {
  assert.equal(isResponsesRoute("GET", "/responses/capabilities"), true);
  assert.equal(isResponsesRoute("GET", "/responses/sessions/search"), true);
  assert.equal(isResponsesRoute("POST", "/responses/sessions/example_123/resume"), true);
  assert.equal(isResponsesRoute("POST", "/responses/sessions/example_123/turns"), true);
  assert.equal(
    isResponsesRoute("GET", `/responses/artifacts/ART-${"a".repeat(32)}`),
    true,
  );
  assert.equal(
    isResponsesRoute("GET", "/responses/artifacts/../../private"),
    false,
  );
  assert.equal(
    isResponsesRoute(
      "POST",
      "/responses/sessions/example_123/approvals/approval_123",
    ),
    true,
  );
  assert.equal(isResponsesRoute("DELETE", "/responses/sessions/example_123"), false);
  assert.equal(isResponsesRoute("POST", "/responses/arbitrary"), false);
  assert.equal(
    deriveResponsesBridgeBaseUrl({
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
    }),
    "https://tools.pj-assistant.ai",
  );
});

test("Full Power proxy streams SSE with only allowlisted bridge headers", async () => {
  let captured = null;
  const response = await handleResponsesProxy(
    new Request("https://pj-assistant.ai/responses/sessions/example_123/turns", {
      method: "POST",
      headers: {
        Authorization: "Bearer browser-credential",
        "Cf-Access-Jwt-Assertion": "access-assertion",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message: "Research this" }),
    }),
    {
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
      PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
    },
    "https://pj-assistant.ai",
    "request-stream",
    async (url, options) => {
      captured = { url, options };
      return new Response(
        'event: completion\ndata: {"type":"completion","text":"Done"}\n\n',
        { headers: { "content-type": "text/event-stream" } },
      );
    },
  );

  assert.equal(captured.url, "https://tools.pj-assistant.ai/responses/sessions/example_123/turns");
  assert.equal(captured.options.headers.authorization, "Bearer bridge-secret");
  assert.equal(captured.options.headers["cf-access-jwt-assertion"], undefined);
  assert.equal(captured.options.headers.accept, "text/event-stream");
  assert.equal(response.headers.get("content-type"), "text/event-stream");
  assert.match(await response.text(), /"type":"completion"/);
});

test("Full Power proxy preserves verified binary downloads with safe filenames", async () => {
  const artifactId = `ART-${"a".repeat(32)}`;
  let captured = null;
  const response = await handleResponsesProxy(
    new Request(`https://pj-assistant.ai/responses/artifacts/${artifactId}`),
    {
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
      PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
    },
    "https://pj-assistant.ai",
    "request-artifact",
    async (url, options) => {
      captured = { url, options };
      return new Response(new Uint8Array([1, 2, 3]), {
        headers: {
          "content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          "content-disposition": 'attachment; filename="../../private/report.docx"',
          etag: '"sha256-abc123"',
          "content-length": "999",
        },
      });
    },
  );

  assert.equal(
    captured.url,
    `https://tools.pj-assistant.ai/responses/artifacts/${artifactId}`,
  );
  assert.equal(captured.options.headers.accept, "application/octet-stream");
  assert.equal(response.headers.get("content-disposition"), 'attachment; filename="report.docx"');
  assert.equal(response.headers.get("etag"), '"sha256-abc123"');
  assert.equal(response.headers.get("content-length"), null);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), new Uint8Array([1, 2, 3]));
});

test("attachment filenames are rebuilt from safe basenames", () => {
  assert.equal(
    safeAttachmentDisposition('attachment; filename="C:\\private\\report.docx"'),
    'attachment; filename="report.docx"',
  );
  assert.equal(safeAttachmentDisposition("inline; filename=report.docx"), null);
  assert.equal(safeAttachmentDisposition('attachment; filename="bad\nname.docx"'), null);
});

test("Access configuration requires team domain, audience, and owner allowlist", () => {
  assert.throws(() => buildAccessConfig({}), /CF_ACCESS_TEAM_DOMAIN/);
  assert.throws(
    () => buildAccessConfig({ CF_ACCESS_TEAM_DOMAIN: TEAM_DOMAIN }),
    /CF_ACCESS_AUD/,
  );
  assert.throws(
    () => buildAccessConfig({
      CF_ACCESS_TEAM_DOMAIN: TEAM_DOMAIN,
      CF_ACCESS_AUD: AUDIENCE,
    }),
    /PJ_OWNER_EMAILS/,
  );
});

test("a valid signed owner assertion is accepted", async () => {
  const result = await validateAccessIdentity(
    assertionRequest(await signAssertion()),
    env,
    certFetch,
  );
  assert.deepEqual(result, {
    ok: true,
    identity: { email: OWNER_EMAIL, subject: "owner-subject" },
  });
});

test("missing, invalid-audience, and expired assertions return typed 401 results", async () => {
  const missing = await validateAccessIdentity(assertionRequest(), env, certFetch);
  assert.equal(missing.status, 401);
  assert.equal(missing.code, "access_authentication_required");

  const wrongAudience = await validateAccessIdentity(
    assertionRequest(await signAssertion({ aud: "wrong-audience" })),
    env,
    certFetch,
  );
  assert.equal(wrongAudience.status, 401);
  assert.equal(wrongAudience.code, "invalid_access_assertion");

  const expired = await validateAccessIdentity(
    assertionRequest(await signAssertion({ exp: NOW_SECONDS - 120 })),
    env,
    certFetch,
  );
  assert.equal(expired.status, 401);
  assert.equal(expired.code, "invalid_access_assertion");
});

test("a valid non-owner identity returns a typed 403 result", async () => {
  const result = await validateAccessIdentity(
    assertionRequest(await signAssertion({ email: "other@example.com" })),
    env,
    certFetch,
  );
  assert.equal(result.status, 403);
  assert.equal(result.code, "access_identity_forbidden");
});

test("privileged routes authenticate before performing work", async () => {
  const response = await worker.fetch(
    new Request("https://pj-assistant.ai/session", {
      method: "POST",
      headers: { Origin: "https://pj-assistant.ai" },
    }),
    env,
  );
  assert.equal(response.status, 401);
  const payload = await response.json();
  assert.equal(payload.error.code, "access_authentication_required");
});

test("public health has security headers and future routes fail closed", async () => {
  const health = await worker.fetch(new Request("https://pj-assistant.ai/health"), env);
  assert.equal(health.status, 200);
  assert.equal(health.headers.get("x-content-type-options"), "nosniff");
  assert.equal(health.headers.get("x-frame-options"), "DENY");
  assert.match(health.headers.get("strict-transport-security"), /max-age=31536000/);

  const futureRoute = await worker.fetch(
    new Request("https://pj-assistant.ai/future-full-power"),
    env,
  );
  assert.equal(futureRoute.status, 401);
});

test("downstream bridge headers contain only the bridge credential", () => {
  const headers = bridgeHeaders({ PJ_TOOL_BRIDGE_TOKEN: "bridge-secret" }, "request-123");
  assert.equal(headers.authorization, "Bearer bridge-secret");
  assert.equal(headers["cf-access-jwt-assertion"], undefined);
  assert.equal(headers["x-pj-client-request-id"], "request-123");

  const unconfiguredHeaders = bridgeHeaders({}, "request-124");
  assert.equal(unconfiguredHeaders.authorization, undefined);
});

test("all Worker responses use the hardened response header policy", () => {
  const headers = responseHeaders("https://pj-assistant.ai", "request-456");
  assert.equal(headers["cache-control"], "no-store");
  assert.equal(headers["content-security-policy"], "default-src 'none'; frame-ancestors 'none'; base-uri 'none'");
  assert.equal(headers["referrer-policy"], "no-referrer");
});

test("realtime excludes long-running tools and aligns bridge timeouts", () => {
  const tools = normalizeFunctionTools([
    {
      type: "function",
      name: "get_current_time",
      parameters: { type: "object", properties: {} },
    },
    {
      type: "function",
      name: "sync_vector_store",
      parameters: { type: "object", properties: {} },
    },
    {
      type: "function",
      name: "delegate_advanced_task",
      parameters: { type: "object", properties: {} },
    },
  ], 10);
  assert.deepEqual(
    tools.map((tool) => tool.name),
    ["get_current_time", "delegate_advanced_task"],
  );
  assert.equal(toolBridgeTimeoutMs("get_current_time"), 85000);
  assert.equal(toolBridgeTimeoutMs("delegate_advanced_task"), 280000);
});

test("browser module initializes with Full Power helpers in shared scope", async () => {
  const html = await readFile(
    new URL("../webrtc_client.html", import.meta.url),
    "utf8",
  );
  const moduleMatch = html.match(/<script type="module">([\s\S]*?)<\/script>/);
  assert.ok(moduleMatch, "browser module script must exist");
  const moduleSource = moduleMatch[1].replace(
    /import\s*\{[\s\S]*?\}\s*from\s*"\/assets\/pj_web_utils\.js";/,
    `const CONTRACT_VERSION = "test";
     const createRequestId = () => "request-test";
     const shorten = (value) => String(value);
     const parseErrorBody = () => "";
     const fetchWithTimeout = async (url) => new Response(
       JSON.stringify(url.endsWith("/health") ? { ok: true } : { tools: [] }),
       { status: 200, headers: { "content-type": "application/json" } },
     );`,
  );

  const listeners = new Map();
  const elements = new Map();
  const makeElement = (id = "") => ({
    id,
    value: "",
    textContent: "",
    innerHTML: "",
    disabled: false,
    scrollTop: 0,
    scrollHeight: 0,
    classList: { toggle() {} },
    appendChild() {},
    replaceChildren() {},
    focus() {},
    querySelectorAll() { return []; },
    addEventListener(type, handler) {
      listeners.set(`${id}:${type}`, handler);
    },
  });
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    createElement() {
      return makeElement();
    },
  };
  const window = {
    location: {
      hostname: "localhost",
      protocol: "http:",
      origin: "http://localhost:3001",
    },
  };
  const localStorage = { getItem() { return null; }, setItem() {} };
  const navigator = { clipboard: { writeText: async () => {} } };
  const initialize = new Function(
    "window",
    "document",
    "localStorage",
    "navigator",
    moduleSource,
  );

  assert.doesNotThrow(() => initialize(
    window,
    document,
    localStorage,
    navigator,
  ));
  assert.equal(typeof listeners.get("startBtn:click"), "function");
  assert.equal(typeof listeners.get("fullPowerModeBtn:click"), "function");
  assert.equal(typeof listeners.get("refreshSessionsBtn:click"), "function");
  assert.match(moduleSource, /event\.type === "artifact\.ready"/);
  assert.match(moduleSource, /className = "artifact-download"/);
  await new Promise((resolve) => setImmediate(resolve));
});
