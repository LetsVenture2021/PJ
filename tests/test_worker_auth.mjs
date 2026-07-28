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
  isPublicRoute,
  responseHeaders,
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
    ["POST", "/future-full-power"],
  ]) {
    assert.equal(isPublicRoute(method, path), false, `${method} ${path} must be privileged`);
  }
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
