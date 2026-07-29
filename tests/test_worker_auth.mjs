import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workerSource = await readFile(
  new URL("../pj_realtime_backend_worker.js", import.meta.url),
  "utf8",
);
const webClientSource = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
const wranglerSource = await readFile(new URL("../wrangler.toml.example", import.meta.url), "utf8");
const webUtilsSource = await readFile(
  new URL("../assets/pj_web_utils.js", import.meta.url),
  "utf8",
);
const workerModule = await import(
  `data:text/javascript;base64,${Buffer.from(workerSource).toString("base64")}`
);
const webUtilsModule = await import(
  `data:text/javascript;base64,${Buffer.from(webUtilsSource).toString("base64")}`
);

const {
  bridgeHeaders,
  buildAccessConfig,
  CONTRACT_VERSION,
  PROTOCOL_VERSION,
  createSessionConfig,
  default: worker,
  deriveResponsesBridgeBaseUrl,
  fetchTextWithTimeout,
  handleSession,
  handleResponsesProxy,
  handleUploadProxy,
  isPublicRoute,
  isResponsesRoute,
  normalizeFunctionTools,
  resolveRealtimeTools,
  requestRealtimeCall,
  requestRealtimeClientSecret,
  responseHeaders,
  safeAttachmentDisposition,
  stableJson,
  toolBridgeTimeoutMs,
  validateAccessIdentity,
  validateProtocolRequest,
} = workerModule;
const { parseErrorBody } = webUtilsModule;

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

test("deployment routes cover APIs without claiming frontend assets", () => {
  for (const route of [
    "pj-assistant.ai/health",
    "pj-assistant.ai/session",
    "pj-assistant.ai/token",
    "pj-assistant.ai/tool-schemas",
    "pj-assistant.ai/execute-tool",
    "pj-assistant.ai/responses/*",
    "pj-assistant.ai/upload/*",
  ]) {
    assert.ok(wranglerSource.includes(`pattern = "${route}"`));
  }
  assert.doesNotMatch(wranglerSource, /pattern = "pj-assistant\.ai\/\*"/);
  assert.doesNotMatch(wranglerSource, /pattern = "pj-assistant\.ai\/webhook"/);
  assert.match(wranglerSource, /POST \/webhook is intentionally not deployed/);
  assert.match(
    wranglerSource,
    /PJ_TOOL_BRIDGE_URL = "https:\/\/replace-with-private-runtime\/execute-tool"/,
  );
  assert.match(webClientSource, /window\.location\.hostname === "www\.pj-assistant\.ai"/);
  assert.match(webClientSource, /\? "https:\/\/pj-assistant\.ai"/);
});

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
    ["POST", "/upload/files"],
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
  assert.equal(isResponsesRoute("GET", `/responses/artifacts/ART-${"a".repeat(32)}`), true);
  assert.equal(isResponsesRoute("GET", "/responses/sessions/example_123/artifacts"), true);
  assert.equal(
    isResponsesRoute("GET", "/responses/artifacts/ART-0123456789abcdef0123456789abcdef"),
    true,
  );
  assert.equal(isResponsesRoute("GET", "/responses/artifacts/../../private"), false);
  assert.equal(
    isResponsesRoute("POST", "/responses/sessions/example_123/approvals/approval_123"),
    true,
  );
  assert.equal(isResponsesRoute("DELETE", "/responses/sessions/example_123"), false);
  assert.equal(isResponsesRoute("POST", "/responses/arbitrary"), false);
  assert.equal(isResponsesRoute("GET", "/responses/artifacts/../../secret"), false);
  assert.equal(
    deriveResponsesBridgeBaseUrl({
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
    }),
    "https://tools.pj-assistant.ai",
  );
});

test("prompt perfecting is an authenticated Full Power bridge route", () => {
  assert.equal(isResponsesRoute("POST", "/responses/prompt-perfect"), true);
  assert.equal(isPublicRoute("POST", "/responses/prompt-perfect"), false);
});

test("upload proxy forwards bounded multipart bodies with allowlisted headers", async () => {
  let captured = null;
  const body = new FormData();
  body.append("session_id", "session_upload_123");
  body.append("paths", "project/brief.txt");
  body.append("files", new Blob(["content"], { type: "text/plain" }), "brief.txt");
  const response = await handleUploadProxy(
    new Request("https://pj-assistant.ai/upload/folder", {
      method: "POST",
      headers: {
        "x-pj-session-id": "session_upload_123",
      },
      body,
    }),
    {
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
      PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
      PJ_MAX_UPLOAD_BYTES: "1024",
    },
    "https://pj-assistant.ai",
    "request-upload",
    async (url, options) => {
      captured = { url, options };
      return new Response(JSON.stringify({ ok: true, count: 1, version: PROTOCOL_VERSION }), {
        status: 201,
        headers: { "content-type": "application/json" },
      });
    },
  );

  assert.equal(captured.url, "https://tools.pj-assistant.ai/upload/folder");
  assert.equal(captured.options.body instanceof ReadableStream, true);
  assert.equal(
    captured.options.headers.authorization,
    bridgeHeaders({ PJ_TOOL_BRIDGE_TOKEN: "bridge-secret" }, "request-upload").authorization,
  );
  assert.equal(captured.options.headers["x-pj-session-id"], "session_upload_123");
  assert.match(captured.options.headers["content-type"], /^multipart\/form-data/);
  assert.equal(response.status, 201);
  assert.equal((await response.json()).count, 1);
});

test("upload proxy maps a non-JSON bridge body to upload_edge_challenged", async () => {
  const body = new FormData();
  body.append("files", new Blob(["content"], { type: "text/plain" }), "brief.txt");
  const response = await handleUploadProxy(
    new Request("https://pj-assistant.ai/upload/files", { method: "POST", body }),
    {
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
      PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
    },
    "https://pj-assistant.ai",
    "request-upload-challenged",
    async () =>
      new Response("<!DOCTYPE html><html><title>Just a moment...</title></html>", {
        status: 403,
        headers: { "content-type": "text/html; charset=UTF-8" },
      }),
  );

  assert.equal(response.status, 502);
  const payload = await response.json();
  assert.equal(payload.error.code, "upload_edge_challenged");
});

test("upload proxy rejects invalid or oversized declared content lengths", async () => {
  const proxyEnv = {
    PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
    PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
    PJ_MAX_UPLOAD_BYTES: "10",
  };
  const invalid = await handleUploadProxy(
    new Request("https://pj-assistant.ai/upload/files", {
      method: "POST",
      headers: {
        "content-type": "multipart/form-data; boundary=boundary",
        "content-length": "invalid",
      },
      body: "body",
    }),
    proxyEnv,
    "https://pj-assistant.ai",
    "request-upload-invalid",
  );
  assert.equal(invalid.status, 400);
  assert.equal((await invalid.json()).error.code, "invalid_upload_length");

  const oversized = await handleUploadProxy(
    new Request("https://pj-assistant.ai/upload/files", {
      method: "POST",
      headers: {
        "content-type": "multipart/form-data; boundary=boundary",
        "content-length": "11",
      },
      body: "body",
    }),
    proxyEnv,
    "https://pj-assistant.ai",
    "request-upload-large",
  );
  assert.equal(oversized.status, 413);
});

test("voice policies keep Fast Voice automatic and Full Power Voice explicit", () => {
  const fast = createSessionConfig("gpt-realtime", env, [], "fast");
  const fullPower = createSessionConfig(
    "gpt-realtime",
    env,
    [],
    "full_power",
    "Authoritative PJ instructions",
  );
  assert.equal(fast.audio.input.turn_detection.create_response, true);
  assert.equal(fullPower.audio.input.turn_detection.create_response, false);
  assert.equal(fullPower.instructions, "Authoritative PJ instructions");
  assert.equal(fast.audio.input.turn_detection.interrupt_response, true);
  assert.equal(fullPower.audio.input.turn_detection.interrupt_response, true);
  assert.equal(isResponsesRoute("POST", "/responses/sessions/example_123/realtime-messages"), true);
});

test("successful session signaling returns the durable session id", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(url, "https://api.openai.com/v1/realtime/calls");
    assert.equal(options.method, "POST");
    assert.match(options.headers.Authorization, /^Bearer /);
    return new Response("v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n", {
      status: 200,
      headers: { "content-type": "application/sdp" },
    });
  };
  try {
    const response = await handleSession(
      new Request(
        "https://pj-assistant.ai/session" + "?session_id=session_voice_123&voice_mode=full_power",
        {
          method: "POST",
          headers: { "content-type": "application/sdp" },
          body: "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
        },
      ),
      {
        OPENAI_API_KEY: "test-openai-key",
        PJ_REALTIME_TOOL_SCHEMAS_JSON: "[]",
      },
      "https://pj-assistant.ai",
      "request-session-success",
    );

    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-pj-session-id"), "session_voice_123");
    assert.equal(response.headers.get("content-type"), "application/sdp");
    assert.match(await response.text(), /^v=0/m);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Realtime bridge validates contract, tool manifest, and instructions", async () => {
  const tools = [
    {
      type: "function",
      name: "example_tool",
      description: "Example — tool",
      parameters: {
        type: "object",
        properties: {},
        required: [],
        additionalProperties: false,
      },
    },
  ];
  const instructions = "Authoritative PJ instructions\n";
  const digest = (value) => createHash("sha256").update(value).digest("hex");
  const payload = {
    contract_version: CONTRACT_VERSION,
    tools,
    tool_manifest_sha256: digest(stableJson(tools)),
    instructions,
    instructions_sha256: digest(instructions),
    prompt_perfecting_version: "1.0",
    tool_policy_sha256: "a".repeat(64),
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  try {
    const bridgeEnv = {
      PJ_TOOL_BRIDGE_URL: "https://bridge.example/execute-tool",
      PJ_TOOL_SCHEMAS_URL: "https://bridge.example/tool-schemas",
      PJ_TOOL_BRIDGE_TOKEN: "bridge-token",
    };
    const bundle = await resolveRealtimeTools(
      bridgeEnv,
      "request-bridge",
      "https://pj-assistant.ai/token",
      true,
    );
    assert.equal(bundle.source, "bridge");
    assert.equal(bundle.tools.length, 1);
    assert.equal(bundle.instructions, instructions);
    assert.equal(bundle.tool_manifest_sha256, payload.tool_manifest_sha256);
    assert.equal(bundle.prompt_perfecting_version, "1.0");
    assert.equal(bundle.tool_policy_sha256, "a".repeat(64));
    const healthResponse = await worker.fetch(
      new Request("https://pj-assistant.ai/health"),
      bridgeEnv,
    );
    const health = await healthResponse.json();
    assert.equal(health.full_tooling_ready, true);
    assert.equal(health.prompt_perfecting_version, "1.0");
    assert.equal(health.tool_policy_sha256, "a".repeat(64));

    payload.tool_manifest_sha256 = "b".repeat(64);
    const rejected = await resolveRealtimeTools(
      bridgeEnv,
      "request-bridge-invalid",
      "https://pj-assistant.ai/token",
      true,
    );
    assert.equal(rejected.source, "bridge_error");
    assert.equal(rejected.tools.length, 0);
    const staleRejected = await resolveRealtimeTools(
      bridgeEnv,
      "request-bridge-stale",
      "https://pj-assistant.ai/token",
    );
    assert.equal(staleRejected.source, "bridge_error");
    assert.equal(staleRejected.tools.length, 0);
    const degradedHealth = await worker
      .fetch(new Request("https://pj-assistant.ai/health"), bridgeEnv)
      .then((response) => response.json());
    assert.equal(degradedHealth.full_tooling_ready, false);
    assert.equal(degradedHealth.tool_schema_reconciliation_status, "failed");
  } finally {
    globalThis.fetch = originalFetch;
  }
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
      return new Response('event: completion\ndata: {"type":"completion","text":"Done"}\n\n', {
        headers: { "content-type": "text/event-stream" },
      });
    },
  );

  assert.equal(captured.url, "https://tools.pj-assistant.ai/responses/sessions/example_123/turns");
  assert.equal(
    captured.options.headers.authorization,
    bridgeHeaders({ PJ_TOOL_BRIDGE_TOKEN: "bridge-secret" }, "request-stream").authorization,
  );
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

  assert.equal(captured.url, `https://tools.pj-assistant.ai/responses/artifacts/${artifactId}`);
  assert.equal(captured.options.headers.accept, "application/octet-stream");
  assert.equal(response.headers.get("content-disposition"), 'attachment; filename="report.docx"');
  assert.equal(response.headers.get("etag"), '"sha256-abc123"');
  assert.equal(response.headers.get("content-length"), null);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), new Uint8Array([1, 2, 3]));
});

test("Full Power proxy preserves binary artifacts and only safe download headers", async () => {
  let captured = null;
  const bytes = new Uint8Array([80, 75, 3, 4, 9, 8, 7]);
  const response = await handleResponsesProxy(
    new Request("https://pj-assistant.ai/responses/artifacts/ART-0123456789abcdef0123456789abcdef"),
    {
      PJ_TOOL_BRIDGE_URL: "https://tools.pj-assistant.ai/execute-tool",
      PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
    },
    "https://pj-assistant.ai",
    "request-artifact",
    async (url, options) => {
      captured = { url, options };
      return new Response(bytes, {
        status: 200,
        headers: {
          "content-type":
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          "content-disposition": 'attachment; filename="deck.pptx"',
          "content-length": String(bytes.byteLength),
          etag: '"sha256-abc123"',
          "x-upstream-secret": "must-not-forward",
        },
      });
    },
  );

  assert.equal(
    captured.url,
    "https://tools.pj-assistant.ai/responses/artifacts/ART-0123456789abcdef0123456789abcdef",
  );
  assert.equal(captured.options.headers.accept, "application/octet-stream");
  assert.equal(
    response.headers.get("content-type"),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  );
  assert.equal(response.headers.get("content-disposition"), 'attachment; filename="deck.pptx"');
  assert.equal(response.headers.get("etag"), '"sha256-abc123"');
  assert.equal(response.headers.get("content-length"), null);
  assert.equal(response.headers.get("x-upstream-secret"), null);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
});

test("attachment filenames are rebuilt from safe basenames", () => {
  assert.equal(
    safeAttachmentDisposition('attachment; filename="C:\\private\\report.docx"'),
    'attachment; filename="report.docx"',
  );
  assert.equal(safeAttachmentDisposition("inline; filename=report.docx"), null);
  assert.equal(safeAttachmentDisposition('attachment; filename="bad\nname.docx"'), null);
});

test("artifact disposition is reconstructed from a validated basename", () => {
  assert.equal(
    safeAttachmentDisposition(
      "attachment; filename=\"../../private/deck.pptx\"; filename*=UTF-8''..%2F..%2Fprivate%2Fdeck.pptx",
    ),
    'attachment; filename="deck.pptx"',
  );
  assert.equal(safeAttachmentDisposition('attachment; filename="bad\nname.pptx"'), null);
  assert.equal(safeAttachmentDisposition("inline; filename=deck.pptx"), null);
});

test("Access configuration requires team domain, audience, and owner allowlist", () => {
  assert.throws(() => buildAccessConfig({}), /CF_ACCESS_TEAM_DOMAIN/);
  assert.throws(() => buildAccessConfig({ CF_ACCESS_TEAM_DOMAIN: TEAM_DOMAIN }), /CF_ACCESS_AUD/);
  assert.throws(
    () =>
      buildAccessConfig({
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

test("public health warms tool schemas and reports n8n readiness", async () => {
  const originalFetch = globalThis.fetch;
  const requestedUrls = [];
  const authorizationHeaders = [];
  const tools = [
    { type: "function", name: "list_n8n_capabilities", parameters: {} },
    { type: "function", name: "get_n8n_corpus_status", parameters: {} },
    { type: "function", name: "get_pj_capability_snapshot", parameters: {} },
  ];
  const instructions = "Authoritative PJ instructions for health warming.\n";
  const digest = (value) => createHash("sha256").update(value).digest("hex");
  const bridgePayload = {
    contract_version: CONTRACT_VERSION,
    tools,
    tool_manifest_sha256: digest(stableJson(tools)),
    instructions,
    instructions_sha256: digest(instructions),
    prompt_perfecting_version: "1.0",
    tool_policy_sha256: "a".repeat(64),
  };
  globalThis.fetch = async (url, options = {}) => {
    requestedUrls.push(String(url));
    authorizationHeaders.push(options.headers.authorization);
    return new Response(JSON.stringify(bridgePayload), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const env = {
    PJ_TOOL_BRIDGE_URL: "https://private-runtime.example/execute-tool",
    PJ_TOOL_BRIDGE_TOKEN: "bridge-secret",
  };
  try {
    // Force-populate the shared module-level schema cache with this test's
    // own bridge payload first (other tests in this file also warm the
    // same cache; forceRefresh keeps this test's assertions independent of
    // suite ordering rather than reading a stale prior test's cache).
    await resolveRealtimeTools(env, "warm-request", "https://pj-assistant.ai/health", true);
    const health = await worker.fetch(new Request("https://pj-assistant.ai/health"), env);
    assert.equal(health.status, 200);
    const payload = await health.json();
    assert.equal(payload.tool_schema_cache_source, "bridge");
    assert.equal(payload.tool_schema_cache_count, 3);
    assert.equal(payload.full_tooling_ready, true);
    assert.equal(payload.n8n_corpus_tools_ready, true);
    assert.deepEqual(requestedUrls, ["https://private-runtime.example/tool-schemas"]);
    assert.equal(authorizationHeaders.length, 1);
    assert.equal(typeof authorizationHeaders[0], "string");
    assert.ok(authorizationHeaders[0].endsWith("bridge-secret"));
  } finally {
    globalThis.fetch = originalFetch;
  }
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
  assert.equal(
    headers["content-security-policy"],
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
  );
  assert.equal(headers["referrer-policy"], "no-referrer");
  assert.match(headers["access-control-expose-headers"], /content-disposition/);
});

test("realtime excludes long-running tools and aligns bridge timeouts", () => {
  const tools = normalizeFunctionTools(
    [
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
    ],
    10,
  );
  assert.deepEqual(
    tools.map((tool) => tool.name),
    ["get_current_time", "delegate_advanced_task"],
  );
  assert.equal(toolBridgeTimeoutMs("get_current_time"), 85000);
  assert.equal(toolBridgeTimeoutMs("delegate_advanced_task"), 280000);
});

test("browser and Worker advertise the same contract version", async () => {
  const source = await readFile(new URL("../assets/pj_web_utils.js", import.meta.url), "utf8");
  const match = source.match(/export const CONTRACT_VERSION = "([^"]+)";/);
  assert.ok(match, "browser contract version must be declared");
  assert.equal(match[1], CONTRACT_VERSION);
});

test("realtime protocol envelopes reject unsupported versions", async () => {
  assert.equal(PROTOCOL_VERSION, 1);
  const supported = await validateProtocolRequest(
    new Request("https://pj-assistant.ai/token", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-pj-protocol-version": String(PROTOCOL_VERSION),
      },
      body: JSON.stringify({ version: PROTOCOL_VERSION }),
    }),
  );
  assert.equal(supported, null);

  const unsupported = await validateProtocolRequest(
    new Request("https://pj-assistant.ai/token", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-pj-protocol-version": "99",
      },
      body: JSON.stringify({ version: 99 }),
    }),
  );
  assert.deepEqual(unsupported, [
    { source: "header", version: "99" },
    { source: "message", version: 99 },
  ]);

  const response = await worker.fetch(
    new Request("https://pj-assistant.ai/token", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-pj-client-request-id": "unsupported-version",
        "x-pj-protocol-version": "99",
      },
      body: JSON.stringify({ version: 99 }),
    }),
    env,
  );
  assert.equal(response.status, 426);
  assert.equal((await response.json()).error.code, "unsupported_protocol_version");
});

test("realtime upstream transport and body-read failures return typed results", async () => {
  const failingFetch = async () => {
    throw new Error("upstream socket closed");
  };
  const call = await requestRealtimeCall(
    "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
    "gpt-realtime",
    { OPENAI_API_KEY: "test-key" },
    [],
    "full_power",
    "Authoritative test instructions",
    failingFetch,
  );
  assert.equal(call.ok, false);
  assert.equal(call.status, 502);
  assert.equal(call.transportError, true);
  assert.match(call.text, /upstream socket closed/);

  const originalFetch = globalThis.fetch;
  globalThis.fetch = failingFetch;
  try {
    const response = await handleSession(
      new Request(
        "https://pj-assistant.ai/session" +
          "?session_id=session_transport_123&voice_mode=full_power",
        {
          method: "POST",
          headers: { "content-type": "application/sdp" },
          body: "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
        },
      ),
      {
        OPENAI_API_KEY: "test-key",
        PJ_REALTIME_INSTRUCTIONS: "Authoritative test instructions",
        PJ_REALTIME_TOOL_SCHEMAS_JSON: "[]",
      },
      "https://pj-assistant.ai",
      "request-session-transport",
    );
    assert.equal(response.status, 502);
    assert.match(response.headers.get("content-type"), /^application\/json/);
    const payload = await response.json();
    assert.equal(payload.error.code, "openai_realtime_unreachable");
    assert.equal(payload.error.request_id, "request-session-transport");
  } finally {
    globalThis.fetch = originalFetch;
  }

  const unreadableResponseFetch = async () => ({
    ok: true,
    status: 200,
    async text() {
      throw new Error("response body stream failed");
    },
  });
  const token = await requestRealtimeClientSecret(
    "gpt-realtime",
    { OPENAI_API_KEY: "test-key" },
    [],
    "fast",
    "Authoritative test instructions",
    unreadableResponseFetch,
  );
  assert.equal(token.ok, false);
  assert.equal(token.status, 502);
  assert.equal(token.transportError, true);
  assert.match(token.text, /response body stream failed/);

  const unreadableCall = await requestRealtimeCall(
    "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
    "gpt-realtime",
    { OPENAI_API_KEY: "test-key" },
    [],
    "fast",
    null,
    unreadableResponseFetch,
  );
  assert.equal(unreadableCall.ok, false);
  assert.equal(unreadableCall.status, 502);
  assert.equal(unreadableCall.transportError, true);
  assert.match(unreadableCall.text, /response body stream failed/);

  await assert.rejects(
    fetchTextWithTimeout("https://api.openai.com/v1/realtime/calls", {}, 10, async () => ({
      async text() {
        return new Promise(() => {});
      },
    })),
    /timeout/,
  );
});

test("HTML infrastructure errors are summarized without leaking markup", () => {
  const detail = parseErrorBody(
    "<!DOCTYPE html><html><head><title>Internal Server Error</title></head><body>failure</body></html>",
  );
  assert.equal(detail, "Server returned an HTML error page (Internal Server Error)");
  assert.doesNotMatch(detail, /<!DOCTYPE|<html|<body/i);
  assert.equal(
    parseErrorBody("<html><body>no title</body></html>"),
    "Server returned an HTML error page",
  );
  assert.equal(
    parseErrorBody(
      JSON.stringify({
        error: {
          message: "Realtime signaling failed.",
          detail:
            "sdp_length=42; <!DOCTYPE html><html><head>" +
            "<title>Bad Gateway</title></head><body>failure</body></html>",
        },
      }),
    ),
    "Server returned an HTML error page (Bad Gateway)",
  );
});

test("HTML error page titles are bounded to a safe length", () => {
  const longTitle = "Upstream Gateway Failure ".repeat(20).trim();
  const detail = parseErrorBody(
    `<!DOCTYPE html><html><head><title>${longTitle}</title></head>` + "<body>failure</body></html>",
  );
  assert.match(detail, /^Server returned an HTML error page \(/);
  const shownTitle = detail.slice("Server returned an HTML error page (".length, -1);
  assert.ok(shownTitle.length <= 163, `title was ${shownTitle.length} chars`);
  assert.match(shownTitle, /\.\.\.$/);
  assert.doesNotMatch(detail, /<!DOCTYPE|<html|<body/i);
});

test("browser module initializes with Full Power helpers in shared scope", async () => {
  const html = await readFile(new URL("../webrtc_client.html", import.meta.url), "utf8");
  const moduleMatch = html.match(/<script type="module">([\s\S]*?)<\/script>/);
  assert.ok(moduleMatch, "browser module script must exist");
  const moduleSource = moduleMatch[1].replace(
    /import\s*\{[\s\S]*?\}\s*from\s*"\/assets\/pj_web_utils\.js[^"]*";/,
    `const CONTRACT_VERSION = "test";
     const PROTOCOL_VERSION = 1;
     const protocolMessage = (payload = {}) => ({ version: PROTOCOL_VERSION, ...payload });
     const assertProtocolResponse = (response, payload = null) => {
       const version = payload?.version ?? response.headers.get("x-pj-protocol-version");
       if (String(version) !== String(PROTOCOL_VERSION)) throw new Error("unsupported protocol");
     };
     const createRequestId = () => "request-test";
     const shorten = (value) => String(value);
     const parseErrorBody = () => "";
     const fetchWithTimeout = async (url, options = {}) => {
       window.__testFetches.push({ url, options });
       const payload = url.endsWith("/execute-tool")
         ? { ...window.__directToolOutput, version: PROTOCOL_VERSION }
         : (url.endsWith("/health")
             ? { ok: true, version: PROTOCOL_VERSION }
             : { tools: [], version: PROTOCOL_VERSION });
       return new Response(
         JSON.stringify(payload),
         { status: 200, headers: { "content-type": "application/json" } },
       );
     };`,
  );

  const listeners = new Map();
  const elements = new Map();
  const makeElement = (id = "") => {
    const element = {
      id,
      value: "",
      textContent: "",
      innerHTML: "",
      disabled: false,
      scrollTop: 0,
      scrollHeight: 0,
      currentTime: 0,
      dataset: {},
      className: "",
      children: [],
      classList: { toggle() {} },
      append(...children) {
        this.children.push(...children);
      },
      appendChild(child) {
        this.children.push(child);
        return child;
      },
      prepend(child) {
        this.children.unshift(child);
      },
      replaceChildren(...children) {
        this.children = children;
      },
      focus() {},
      pause() {},
      play() {
        return Promise.resolve();
      },
      remove() {
        this.removed = true;
      },
      querySelector(selector) {
        const className = selector.startsWith(".") ? selector.slice(1) : "";
        return (
          this.children.find((child) =>
            String(child.className || "")
              .split(/\s+/)
              .includes(className),
          ) || null
        );
      },
      querySelectorAll() {
        return [];
      },
      addEventListener(type, handler) {
        listeners.set(`${id}:${type}`, handler);
      },
    };
    return element;
  };
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
    __testFetches: [],
    __directToolOutput: null,
    location: {
      hostname: "localhost",
      protocol: "http:",
      origin: "http://localhost:3001",
    },
  };
  const localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };
  const navigator = { clipboard: { writeText: async () => {} } };
  const initialize = new Function(
    "window",
    "document",
    "localStorage",
    "navigator",
    `${moduleSource}
     return {
       state,
       handleRealtimeEvent,
       renderArtifactCard,
       runFunctionCall,
       sendRealtimeEvent,
       seedRealtimeConversation,
       shouldUseEphemeralSignalingFallback,
       validateInboundRealtimeEvent,
       validateOutboundRealtimeEvent,
     };`,
  );

  const hooks = initialize(window, document, localStorage, navigator);
  assert.ok(hooks);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(500, ""), true);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(503, "gateway unavailable"), true);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(599, "upstream failure"), true);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(599, "<html>failure</html>"), true);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(400, "invalid_offer"), true);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(400, "invalid model"), false);
  assert.equal(hooks.shouldUseEphemeralSignalingFallback(429, "rate limited"), false);
  assert.equal(typeof listeners.get("startBtn:click"), "function");
  assert.equal(typeof listeners.get("fullPowerModeBtn:click"), "function");
  assert.equal(typeof listeners.get("fullPowerVoiceModeBtn:click"), "function");
  assert.equal(typeof listeners.get("refreshSessionsBtn:click"), "function");
  assert.match(moduleSource, /event\.type === "artifact\.ready"/);
  assert.match(moduleSource, /className = "artifact-download"/);
  assert.match(html, /className = "artifact-card"/);
  assert.match(html, /fullPowerToolRows: new Map\(\)/);
  assert.match(html, /realtimeItems: new Map\(\)/);
  assert.match(html, /processedRealtimeEventIds: new Set\(\)/);
  assert.match(html, /conversation\.item\.truncate/);
  assert.match(html, /conversation\.item\.delete/);
  assert.match(html, /persistedRealtimeStates: new Map\(\)/);
  assert.match(html, /seededSessionIds\.delete\(sessionId\)/);
  assert.match(html, /awaiting_response_status/);
  assert.match(html, /activePlaybackStartSeconds/);
  assert.doesNotMatch(html, /: `\$\{prefix\}_\$\{crypto\.randomUUID\(\)/);
  assert.doesNotMatch(html, /persistAssistantRealtimeItem\(item, "completed"\)/);
  assert.match(html, /artifact-image-preview/);
  assert.match(html, /startsWith\("image\/"\)/);
  assert.doesNotMatch(html, /innerHTML\s*=\s*event\./);
  assert.match(
    hooks.validateInboundRealtimeEvent({
      type: "response.output_audio_transcript.delta",
      item_id: "assistant-missing-delta",
    }),
    /delta/,
  );
  assert.match(hooks.validateInboundRealtimeEvent(null), /object/);
  assert.match(hooks.validateOutboundRealtimeEvent({ type: "conversation.item.create" }), /item/);
  assert.throws(
    () => hooks.sendRealtimeEvent({ type: "conversation.item.delete" }),
    /Invalid outbound realtime payload: item_id is required/,
  );

  hooks.state.activeSessionId = "session_behavior";
  hooks.handleRealtimeEvent({
    event_id: "input-delta-1",
    type: "conversation.item.input_audio_transcription.delta",
    item_id: "user-item-1",
    delta: "Hel",
  });
  hooks.handleRealtimeEvent({
    event_id: "input-delta-1",
    type: "conversation.item.input_audio_transcription.delta",
    item_id: "user-item-1",
    delta: "Hel",
  });
  assert.equal(hooks.state.realtimeItems.get("user-item-1").text, "Hel");
  hooks.handleRealtimeEvent({
    event_id: "input-done-1",
    type: "conversation.item.input_audio_transcription.completed",
    item_id: "user-item-1",
    transcript: "Hello PJ",
  });

  hooks.handleRealtimeEvent({
    event_id: "response-created-1",
    type: "response.created",
    response: { id: "response-1" },
  });
  hooks.handleRealtimeEvent({
    event_id: "output-delta-1",
    type: "response.output_audio_transcript.delta",
    item_id: "assistant-item-1",
    response_id: "response-1",
    delta: "Hi",
  });
  hooks.handleRealtimeEvent({
    event_id: "output-delta-1",
    type: "response.output_audio_transcript.delta",
    item_id: "assistant-item-1",
    response_id: "response-1",
    delta: "Hi",
  });
  assert.equal(hooks.state.realtimeItems.get("assistant-item-1").text, "Hi");
  hooks.handleRealtimeEvent({
    event_id: "output-done-1",
    type: "response.output_audio_transcript.done",
    item_id: "assistant-item-1",
    response_id: "response-1",
    transcript: "Hi there",
  });
  assert.equal(
    hooks.state.realtimeItems.get("assistant-item-1").status,
    "awaiting_response_status",
  );
  const responseDone = {
    type: "response.done",
    response: {
      id: "response-1",
      status: "completed",
      output: [{ id: "assistant-item-1" }],
    },
  };
  hooks.handleRealtimeEvent({ event_id: "response-done-1", ...responseDone });
  hooks.handleRealtimeEvent({ event_id: "response-done-2", ...responseDone });

  const audio = elements.get("pjAudio");
  const sent = [];
  hooks.state.dataChannel = {
    readyState: "open",
    send(payload) {
      sent.push(JSON.parse(payload));
    },
  };
  hooks.handleRealtimeEvent({
    event_id: "response-created-2",
    type: "response.created",
    response: { id: "response-2" },
  });
  hooks.handleRealtimeEvent({
    event_id: "output-delta-2",
    type: "response.output_audio_transcript.delta",
    item_id: "assistant-item-2",
    response_id: "response-2",
    delta: "This will be interrupted",
  });
  audio.currentTime = 3.25;
  hooks.state.activePlaybackStartSeconds = 2;
  hooks.handleRealtimeEvent({
    event_id: "speech-started-1",
    type: "input_audio_buffer.speech_started",
  });
  assert.deepEqual(
    sent.map((event) => event.type),
    ["conversation.item.truncate", "response.cancel"],
  );
  assert.equal(sent[0].audio_end_ms, 1250);
  assert.equal(hooks.state.realtimeItems.get("assistant-item-2").status, "interrupted");
  hooks.handleRealtimeEvent({
    event_id: "output-done-after-interruption",
    type: "response.output_audio_transcript.done",
    item_id: "assistant-item-2",
    response_id: "response-2",
    transcript: "This will be interrupted",
  });
  assert.equal(hooks.state.realtimeItems.get("assistant-item-2").status, "interrupted");
  const interruptedDone = {
    type: "response.done",
    response: {
      id: "response-2",
      status: "completed",
      output: [{ id: "assistant-item-2" }],
    },
  };
  hooks.handleRealtimeEvent({
    event_id: "response-done-interrupted-1",
    ...interruptedDone,
  });
  hooks.handleRealtimeEvent({
    event_id: "response-done-interrupted-2",
    ...interruptedDone,
  });

  hooks.renderArtifactCard({
    artifact_id: "ART-0123456789abcdef0123456789abcdef",
    filename: "generated.png",
    download_url: "/responses/artifacts/ART-0123456789abcdef0123456789abcdef",
    format: "png",
    mime_type: "image/png",
    byte_size: 100,
    sha256: "a".repeat(64),
  });
  const artifactCard = hooks.state.artifactCards.get("ART-0123456789abcdef0123456789abcdef");
  assert.ok(artifactCard.querySelector(".artifact-image-preview"));

  const directArtifactId = "ART-fedcba9876543210fedcba9876543210";
  window.__directToolOutput = {
    artifact: {
      artifact_id: directArtifactId,
      filename: "direct-realtime.pptx",
      download_url: `/responses/artifacts/${directArtifactId}`,
      format: "pptx",
      mime_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      byte_size: 2048,
      sha256: "b".repeat(64),
      status: "ready",
    },
  };
  await hooks.runFunctionCall(
    "call-direct-artifact",
    "export_document",
    "{}",
    "http://localhost:3001",
  );
  assert.ok(hooks.state.artifactCards.get(directArtifactId));
  assert.ok(window.__testFetches.some(({ url }) => url === "http://localhost:3001/execute-tool"));

  const seedSent = [];
  hooks.state.pendingSeedHistory = [
    { role: "user", content: "Persisted request", status: "completed" },
    { role: "assistant", content: "Unheard output", status: "interrupted" },
    { role: "assistant", content: "Persisted answer", status: "completed" },
  ];
  hooks.state.seededSessionIds.delete("session_behavior");
  hooks.state.dataChannel = {
    readyState: "open",
    send(payload) {
      seedSent.push(JSON.parse(payload));
    },
  };
  hooks.seedRealtimeConversation();
  hooks.seedRealtimeConversation();
  assert.equal(seedSent.length, 2);
  assert.deepEqual(
    seedSent.map((event) => event.item.role),
    ["user", "assistant"],
  );

  await new Promise((resolve) => setImmediate(resolve));
  const persisted = window.__testFetches.filter(
    ({ url, options }) => url.includes("/realtime-messages") && options.method === "POST",
  );
  const persistedBodies = persisted.map(({ options }) => JSON.parse(options.body));
  assert.equal(
    persistedBodies.filter(
      (body) => body.external_id === "assistant-item-1" && body.status === "completed",
    ).length,
    1,
  );
  assert.equal(
    persistedBodies.filter(
      (body) => body.external_id === "assistant-item-2" && body.status === "interrupted",
    ).length,
    1,
  );
  assert.ok(
    persistedBodies.some(
      (body) => body.external_id === "user-item-1" && body.content === "Hello PJ",
    ),
  );
});
