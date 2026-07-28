const CONTRACT_VERSION = "2026-07-28.6";
const DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1";
const FALLBACK_REALTIME_MODEL = "gpt-realtime";
const REALTIME_VOICE = "marin";
const MAX_ERROR_DETAIL_LENGTH = 320;
const DEFAULT_TOOL_SCHEMA_CACHE_TTL_MS = 60000;
const DEFAULT_MAX_REALTIME_TOOLS = 256;
const DEFAULT_ACCESS_CERT_CACHE_TTL_MS = 300000;
const REALTIME_CALL_TIMEOUT_MS = 30000;
const REALTIME_TOKEN_TIMEOUT_MS = 12000;
const ACCESS_CLOCK_SKEW_SECONDS = 60;
const MAX_RESPONSES_REQUEST_BYTES = 262144;
const REALTIME_EXCLUDED_TOOL_NAMES = new Set([
  "approve_codeops_task",
  "create_skill",
  "learn_from_vector_store",
  "run_codeops_validation",
  "run_shortcut",
  "sync_vector_store",
  "generate_image_asset",
  "edit_image_asset",
  "create_image_variation",
  "create_controlled_image",
  "register_vector_image",
  "delete_image_asset",
]);

// Public: GET /health and CORS preflight. Every other route is privileged so
// future Full Power endpoints fail closed until they explicitly authenticate.
const PUBLIC_ROUTES = new Set(["GET /health"]);

let toolSchemaCache = {
  tools: [],
  source: "cold_start",
  detail: null,
  instructions: null,
  instructions_sha256: null,
  tool_manifest_sha256: null,
  prompt_perfecting_version: null,
  tool_policy_sha256: null,
  fetched_at_ms: 0,
};
let toolSchemaReconciliation = {
  status: "never",
  attempted_at_ms: 0,
};

function bridgeSchemaFailure(detail, source = "bridge_error") {
  toolSchemaReconciliation = {
    status: "failed",
    attempted_at_ms: Date.now(),
  };
  return {
    tools: [],
    source,
    detail,
    instructions: null,
  };
}
const accessCertCache = new Map();

const DEFAULT_INSTRUCTIONS =
  "You are PJ, a helpful realtime voice assistant. Keep responses concise and actionable. " +
  "If the user speaks in another language, respond in that same language unless asked otherwise.";

function buildAllowedOrigins(env) {
  const raw =
    env.PJ_ALLOWED_ORIGINS ||
    "https://pj-assistant.ai,https://www.pj-assistant.ai,http://localhost:3001,http://127.0.0.1:3001,http://localhost:5173,http://127.0.0.1:5173";
  return raw
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

function isAllowedOrigin(origin, allowedOrigins) {
  return allowedOrigins.includes(origin);
}

function normalizedOriginFromReferer(referer) {
  if (!referer) {
    return null;
  }
  try {
    return new URL(referer).origin;
  } catch {
    return null;
  }
}

function pickCorsOrigin(request, allowedOrigins) {
  const origin = request.headers.get("origin");
  if (!origin) {
    return "*";
  }
  return isAllowedOrigin(origin, allowedOrigins) ? origin : null;
}

function corsHeaders(corsOrigin) {
  return {
    "access-control-allow-origin": corsOrigin,
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers":
      "content-type,authorization,x-pj-client-request-id,x-pj-contract-version",
    "access-control-expose-headers":
      "x-request-id,x-pj-contract-version,content-disposition,content-length,etag",
  };
}

function responseHeaders(corsOrigin, requestId, contentType = "application/json") {
  return {
    "content-type": contentType,
    "cache-control": "no-store",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-request-id": requestId,
    "x-pj-contract-version": CONTRACT_VERSION,
    vary: "Origin",
    ...corsHeaders(corsOrigin),
  };
}

function safeAttachmentDisposition(value) {
  if (typeof value !== "string" || !/^attachment(?:;|$)/i.test(value)) {
    return null;
  }
  const encoded = value.match(/filename\*\s*=\s*UTF-8''([^;]+)/i);
  const quoted = value.match(/filename\s*=\s*"([^"]*)"/i);
  const bare = value.match(/filename\s*=\s*([^;]+)/i);
  let candidate = encoded?.[1] || quoted?.[1] || bare?.[1] || "";
  try {
    candidate = decodeURIComponent(candidate.trim());
  } catch {
    return null;
  }
  const basename = candidate.replaceAll("\\", "/").split("/").pop()?.trim() || "";
  if (
    !/^[A-Za-z0-9][A-Za-z0-9._ -]{0,159}$/.test(basename) ||
    basename === "." ||
    basename === ".."
  ) {
    return null;
  }
  return `attachment; filename="${basename.replaceAll('"', "")}"`;
}

function trimDetail(detail) {
  if (!detail) {
    return null;
  }
  const compact = String(detail).replace(/\s+/g, " ").trim();
  if (!compact) {
    return null;
  }
  if (compact.length <= MAX_ERROR_DETAIL_LENGTH) {
    return compact;
  }
  return `${compact.slice(0, MAX_ERROR_DETAIL_LENGTH)}...`;
}

function errorPayload(code, message, requestId, detail = null) {
  return {
    ok: false,
    error: {
      code,
      message,
      request_id: requestId,
      detail,
    },
  };
}

function jsonResponse(payload, status, corsOrigin, requestId) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: responseHeaders(corsOrigin, requestId, "application/json"),
  });
}

function logEvent(requestId, event, data = {}) {
  console.log(
    JSON.stringify({
      request_id: requestId,
      event,
      ...data,
    }),
  );
}

function isPublicRoute(method, pathname) {
  return method === "OPTIONS" || PUBLIC_ROUTES.has(`${method} ${pathname}`);
}

function splitConfigList(value) {
  return String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function normalizeAccessTeamDomain(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) {
    throw new Error("CF_ACCESS_TEAM_DOMAIN is required");
  }

  let hostname = raw;
  if (raw.includes("://")) {
    const url = new URL(raw);
    if (url.protocol !== "https:" || (url.pathname !== "/" && url.pathname !== "")) {
      throw new Error("CF_ACCESS_TEAM_DOMAIN must be an HTTPS Cloudflare Access team domain");
    }
    hostname = url.hostname;
  } else if (!raw.includes(".")) {
    hostname = `${raw}.cloudflareaccess.com`;
  }

  if (!/^[a-z0-9.-]+$/.test(hostname) || !hostname.endsWith(".cloudflareaccess.com")) {
    throw new Error("CF_ACCESS_TEAM_DOMAIN must end in .cloudflareaccess.com");
  }
  return hostname;
}

function buildAccessConfig(env) {
  const teamDomain = normalizeAccessTeamDomain(env.CF_ACCESS_TEAM_DOMAIN);
  const audiences = splitConfigList(env.CF_ACCESS_AUD);
  const ownerEmails = splitConfigList(env.PJ_OWNER_EMAILS).map((email) => email.toLowerCase());
  if (!audiences.length) {
    throw new Error("CF_ACCESS_AUD is required");
  }
  if (!ownerEmails.length) {
    throw new Error("PJ_OWNER_EMAILS is required");
  }

  const issuer = `https://${teamDomain}`;
  return {
    issuer,
    certsUrl: `${issuer}/cdn-cgi/access/certs`,
    audiences: new Set(audiences),
    ownerEmails: new Set(ownerEmails),
    certCacheTtlMs: asPositiveInt(
      env.CF_ACCESS_CERT_CACHE_TTL_MS,
      DEFAULT_ACCESS_CERT_CACHE_TTL_MS,
    ),
  };
}

function decodeBase64Url(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error("Invalid base64url value");
  }
  const padded = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

function decodeJwtSection(value) {
  return JSON.parse(new TextDecoder().decode(decodeBase64Url(value)));
}

function parseAccessJwt(assertion) {
  const parts = String(assertion || "").split(".");
  if (parts.length !== 3 || parts.some((part) => !part)) {
    throw new Error("Malformed Access assertion");
  }
  const header = decodeJwtSection(parts[0]);
  const claims = decodeJwtSection(parts[1]);
  if (!header || typeof header !== "object" || !claims || typeof claims !== "object") {
    throw new Error("Malformed Access assertion");
  }
  if (header.alg !== "RS256" || typeof header.kid !== "string" || !header.kid) {
    throw new Error("Unsupported Access assertion signature");
  }
  return {
    header,
    claims,
    signingInput: new TextEncoder().encode(`${parts[0]}.${parts[1]}`),
    signature: decodeBase64Url(parts[2]),
  };
}

function claimHasExpectedAudience(claim, expectedAudiences) {
  const tokenAudiences = Array.isArray(claim) ? claim : [claim];
  return tokenAudiences.some(
    (audience) => typeof audience === "string" && expectedAudiences.has(audience),
  );
}

function validateAccessClaims(claims, config, nowSeconds = Math.floor(Date.now() / 1000)) {
  if (claims.iss !== config.issuer) {
    throw new Error("Access assertion issuer mismatch");
  }
  if (!claimHasExpectedAudience(claims.aud, config.audiences)) {
    throw new Error("Access assertion audience mismatch");
  }
  if (typeof claims.exp !== "number" || claims.exp <= nowSeconds - ACCESS_CLOCK_SKEW_SECONDS) {
    throw new Error("Access assertion expired");
  }
  if (claims.nbf !== undefined &&
      (typeof claims.nbf !== "number" || claims.nbf > nowSeconds + ACCESS_CLOCK_SKEW_SECONDS)) {
    throw new Error("Access assertion is not active");
  }
  if (claims.iat !== undefined &&
      (typeof claims.iat !== "number" || claims.iat > nowSeconds + ACCESS_CLOCK_SKEW_SECONDS)) {
    throw new Error("Access assertion issued in the future");
  }
  if (typeof claims.email !== "string" || !claims.email.trim()) {
    throw new Error("Access assertion has no email identity");
  }
  return claims.email.trim().toLowerCase();
}

async function fetchAccessCerts(config, fetchImpl, forceRefresh = false) {
  const cached = accessCertCache.get(config.certsUrl);
  if (!forceRefresh && cached && Date.now() - cached.fetchedAtMs < config.certCacheTtlMs) {
    return cached.keys;
  }

  const response = await fetchImpl(config.certsUrl, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`Access cert endpoint returned ${response.status}`);
  }
  const payload = await response.json();
  const keys = Array.isArray(payload?.keys) ? payload.keys : [];
  if (!keys.length) {
    throw new Error("Access cert endpoint returned no keys");
  }
  accessCertCache.set(config.certsUrl, { keys, fetchedAtMs: Date.now() });
  return keys;
}

async function findAccessCert(config, kid, fetchImpl) {
  let keys = await fetchAccessCerts(config, fetchImpl);
  let key = keys.find((candidate) => candidate?.kid === kid);
  if (!key) {
    keys = await fetchAccessCerts(config, fetchImpl, true);
    key = keys.find((candidate) => candidate?.kid === kid);
  }
  if (!key) {
    throw new Error("Access signing key not found");
  }
  return key;
}

async function validateAccessIdentity(request, env, fetchImpl = fetch) {
  let config;
  try {
    config = buildAccessConfig(env);
  } catch {
    return {
      ok: false,
      status: 503,
      code: "access_configuration_error",
      message: "Cloudflare Access authentication is not configured.",
    };
  }

  const assertion = request.headers.get("cf-access-jwt-assertion");
  if (!assertion) {
    return {
      ok: false,
      status: 401,
      code: "access_authentication_required",
      message: "A Cloudflare Access identity is required.",
    };
  }

  try {
    const jwt = parseAccessJwt(assertion);
    const jwk = await findAccessCert(config, jwt.header.kid, fetchImpl);
    const publicKey = await crypto.subtle.importKey(
      "jwk",
      jwk,
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["verify"],
    );
    const signatureValid = await crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5",
      publicKey,
      jwt.signature,
      jwt.signingInput,
    );
    if (!signatureValid) {
      throw new Error("Access assertion signature mismatch");
    }
    const email = validateAccessClaims(jwt.claims, config);
    if (!config.ownerEmails.has(email)) {
      return {
        ok: false,
        status: 403,
        code: "access_identity_forbidden",
        message: "The authenticated Access identity is not authorized.",
      };
    }
    return { ok: true, identity: { email, subject: jwt.claims.sub || null } };
  } catch {
    return {
      ok: false,
      status: 401,
      code: "invalid_access_assertion",
      message: "The Cloudflare Access assertion is invalid.",
    };
  }
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 20000, fetchImpl = fetch) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error("timeout")), timeoutMs);
  try {
    return await fetchImpl(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchTextWithTimeout(
  url,
  options = {},
  timeoutMs = 20000,
  fetchImpl = fetch,
) {
  const controller = new AbortController();
  const timeoutError = new Error("timeout");
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => {
      controller.abort(timeoutError);
      reject(timeoutError);
    }, timeoutMs);
  });
  const request = (async () => {
    const response = await fetchImpl(url, {
      ...options,
      signal: controller.signal,
    });
    return {
      response,
      text: await response.text(),
    };
  })();
  try {
    return await Promise.race([request, deadline]);
  } finally {
    clearTimeout(timeout);
  }
}

function createSessionConfig(
  model,
  env,
  tools,
  voiceMode = "fast",
  authoritativeInstructions = null,
) {
  if (!["fast", "full_power"].includes(voiceMode)) {
    throw new Error("voiceMode must be fast or full_power");
  }
  return {
    type: "realtime",
    model,
    instructions:
      authoritativeInstructions
      || env.PJ_REALTIME_INSTRUCTIONS
      || DEFAULT_INSTRUCTIONS,
    audio: {
      input: {
        transcription: { model: "gpt-4o-transcribe" },
        turn_detection: {
          type: "server_vad",
          silence_duration_ms: 500,
          create_response: voiceMode === "fast",
          interrupt_response: true,
        },
      },
      output: { voice: REALTIME_VOICE },
    },
    tools,
  };
}

function asPositiveInt(value, fallbackValue) {
  const parsed = Number.parseInt(String(value || ""), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallbackValue;
}

function normalizeFunctionTools(rawTools, maxTools) {
  if (!Array.isArray(rawTools)) {
    return [];
  }
  const normalized = [];
  for (const item of rawTools) {
    if (!item || typeof item !== "object") {
      continue;
    }
    if (item.type !== "function") {
      continue;
    }
    const name = typeof item.name === "string" ? item.name.trim() : "";
    if (!name) {
      continue;
    }
    if (REALTIME_EXCLUDED_TOOL_NAMES.has(name)) {
      continue;
    }
    const parameters =
      item.parameters && typeof item.parameters === "object"
        ? item.parameters
        : { type: "object", properties: {}, required: [] };
    normalized.push({
      type: "function",
      name,
      description: typeof item.description === "string" ? item.description : "",
      parameters,
    });
    if (normalized.length >= maxTools) {
      break;
    }
  }
  return normalized;
}

function toolBridgeTimeoutMs(toolName) {
  return toolName === "delegate_advanced_task" ? 280000 : 85000;
}

function parseToolSchemaPayload(rawText, maxTools) {
  let parsed = null;
  try {
    parsed = rawText ? JSON.parse(rawText) : null;
  } catch {
    parsed = null;
  }
  if (Array.isArray(parsed)) {
    return normalizeFunctionTools(parsed, maxTools);
  }
  if (parsed && Array.isArray(parsed.tools)) {
    return normalizeFunctionTools(parsed.tools, maxTools);
  }
  return [];
}

async function sha256Hex(value) {
  const data = new TextEncoder().encode(String(value));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function bridgeHeaders(env, requestId) {
  // Construct a fresh allowlisted header set so Access assertions and browser
  // credentials can never be forwarded to the private tool runtime.
  const headers = {
    "content-type": "application/json",
    "x-pj-client-request-id": requestId,
    "x-pj-contract-version": CONTRACT_VERSION,
  };
  if (env.PJ_TOOL_BRIDGE_TOKEN) {
    headers.authorization = `Bearer ${env.PJ_TOOL_BRIDGE_TOKEN}`;
  }
  return headers;
}

function deriveToolSchemasUrl(env) {
  const explicit = (env.PJ_TOOL_SCHEMAS_URL || "").trim();
  if (explicit) {
    return explicit;
  }
  const bridgeUrl = (env.PJ_TOOL_BRIDGE_URL || "").trim();
  if (!bridgeUrl) {
    return "";
  }
  if (bridgeUrl.includes("/execute-tool")) {
    return bridgeUrl.replace("/execute-tool", "/tool-schemas");
  }
  return `${bridgeUrl.replace(/\/+$/, "")}/tool-schemas`;
}

function deriveResponsesBridgeBaseUrl(env) {
    const raw = (
      env.PJ_RESPONSES_BRIDGE_URL ||
      env.PJ_TOOL_BRIDGE_URL ||
      ""
    ).trim();
    if (!raw) {
      return "";
    }
    let target;
    try {
      target = new URL(raw);
    } catch {
      return "";
    }
    target.search = "";
    target.hash = "";
    target.pathname = target.pathname
      .replace(/\/(?:execute-tool|tool-schemas)\/?$/, "")
      .replace(/\/+$/, "");
    return target.toString().replace(/\/+$/, "");
  }

function isResponsesRoute(method, pathname) {
    if (method === "GET" && pathname === "/responses/capabilities") {
      return true;
    }
    if (method === "POST" && pathname === "/responses/prompt-perfect") {
      return true;
    }
    if ((method === "GET" || method === "POST") && pathname === "/responses/sessions") {
      return true;
    }
    if (method === "GET" && pathname === "/responses/sessions/search") {
      return true;
    }
    if (
      method === "GET" &&
      (
        /^\/responses\/artifacts\/ART-[a-f0-9]{32}$/.test(pathname) ||
        /^\/responses\/sessions\/[A-Za-z0-9_-]{8,128}\/artifacts$/.test(pathname)
      )
    ) {
      return true;
    }
    return method === "POST" && (
      /^\/responses\/sessions\/[A-Za-z0-9_-]{8,128}\/resume$/.test(pathname) ||
      /^\/responses\/sessions\/[A-Za-z0-9_-]{8,128}\/turns$/.test(pathname) ||
      /^\/responses\/sessions\/[A-Za-z0-9_-]{8,128}\/realtime-messages$/.test(pathname) ||
      /^\/responses\/sessions\/[A-Za-z0-9_-]{8,128}\/approvals\/[A-Za-z0-9_-]{8,128}$/.test(pathname)
    );
  }

async function handleResponsesProxy(
    request,
    env,
    corsOrigin,
    requestId,
    fetchImpl = fetch,
  ) {
    const inboundUrl = new URL(request.url);
    if (!isResponsesRoute(request.method, inboundUrl.pathname)) {
      return jsonResponse(
        errorPayload("not_found", "Not found.", requestId),
        404,
        corsOrigin,
        requestId,
      );
    }
    if (!(env.PJ_TOOL_BRIDGE_TOKEN || "").trim()) {
      return jsonResponse(
        errorPayload(
          "bridge_auth_not_configured",
          "The private runtime credential is not configured.",
          requestId,
        ),
        503,
        corsOrigin,
        requestId,
      );
    }

    let bridgeBase;
    try {
      bridgeBase = deriveResponsesBridgeBaseUrl(env);
    } catch {
      bridgeBase = "";
    }
    if (!bridgeBase) {
      return jsonResponse(
        errorPayload(
          "responses_bridge_not_configured",
          "The Full Power runtime bridge is not configured.",
          requestId,
        ),
        503,
        corsOrigin,
        requestId,
      );
    }

    const target = new URL(bridgeBase);
    target.pathname = `${target.pathname.replace(/\/+$/, "")}${inboundUrl.pathname}`;
    target.search = inboundUrl.search;
    if (isLoopTarget(target.toString(), request.url)) {
      return jsonResponse(
        errorPayload(
          "bridge_loop_detected",
          "The Full Power runtime bridge would recurse.",
          requestId,
        ),
        500,
        corsOrigin,
        requestId,
      );
    }

    const isStreamingTurn = /\/turns$/.test(inboundUrl.pathname);
    const isArtifactDownload =
      /^\/responses\/artifacts\/ART-[a-f0-9]{32}$/.test(inboundUrl.pathname);
    const headers = {
      ...bridgeHeaders(env, requestId),
      accept: isStreamingTurn
        ? "text/event-stream"
        : (isArtifactDownload ? "application/octet-stream" : "application/json"),
    };
    let body;
    if (request.method === "POST") {
      body = await request.text();
      if (new TextEncoder().encode(body).byteLength > MAX_RESPONSES_REQUEST_BYTES) {
        return jsonResponse(
          errorPayload(
            "request_too_large",
            `Request body exceeds ${MAX_RESPONSES_REQUEST_BYTES} bytes.`,
            requestId,
          ),
          413,
          corsOrigin,
          requestId,
        );
      }
    }

    try {
      const bridgeResponse = await fetchImpl(target.toString(), {
        method: request.method,
        headers,
        ...(body !== undefined ? { body } : {}),
      });
      const upstreamContentType =
        bridgeResponse.headers.get("content-type") ||
        (isStreamingTurn ? "text/event-stream" : "application/json");
      const isEventStream = upstreamContentType.includes("text/event-stream");
      const isJson = upstreamContentType.includes("application/json");
      const contentType = isEventStream
        ? "text/event-stream"
        : (isArtifactDownload && !isJson
          ? upstreamContentType.split(";")[0].trim()
          : "application/json");
      const responseHeaderSet = responseHeaders(corsOrigin, requestId, contentType);
      if (isEventStream) {
        responseHeaderSet["x-accel-buffering"] = "no";
      }
      if (isArtifactDownload && !isJson) {
        const safeDisposition = safeAttachmentDisposition(
          bridgeResponse.headers.get("content-disposition") || "",
        );
        const etag = bridgeResponse.headers.get("etag") || "";
        if (safeDisposition) {
          responseHeaderSet["content-disposition"] = safeDisposition;
        }
        if (/^"[A-Za-z0-9._-]{1,160}"$/.test(etag)) {
          responseHeaderSet.etag = etag;
        }
      }
      logEvent(requestId, "responses.bridge_complete", {
        path: inboundUrl.pathname,
        status: bridgeResponse.status,
        streaming: isEventStream,
        artifact: isArtifactDownload && !isJson,
      });
      return new Response(bridgeResponse.body, {
        status: bridgeResponse.status,
        headers: responseHeaderSet,
      });
    } catch (exc) {
      return jsonResponse(
        errorPayload(
          "responses_bridge_unreachable",
          "The Full Power runtime request failed before completion.",
          requestId,
          trimDetail(exc),
        ),
        502,
        corsOrigin,
        requestId,
      );
    }
}

function isLoopTarget(targetUrl, requestUrl) {
  if (!requestUrl) {
    return false;
  }
  try {
    const target = new URL(targetUrl);
    const current = new URL(requestUrl);
    return target.origin === current.origin && target.pathname === current.pathname;
  } catch {
    return false;
  }
}

async function resolveRealtimeTools(env, requestId, requestUrl, forceRefresh = false) {
  const maxTools = asPositiveInt(env.PJ_MAX_REALTIME_TOOLS, DEFAULT_MAX_REALTIME_TOOLS);
  const cacheTtlMs = asPositiveInt(env.PJ_TOOL_SCHEMA_CACHE_TTL_MS, DEFAULT_TOOL_SCHEMA_CACHE_TTL_MS);

  const inlineToolsRaw = (env.PJ_REALTIME_TOOL_SCHEMAS_JSON || "").trim();
  if (inlineToolsRaw) {
    const inlineTools = parseToolSchemaPayload(inlineToolsRaw, maxTools);
    return {
      tools: inlineTools,
      source: "inline_env",
      detail: inlineTools.length ? null : "inline schema env var was present but empty/invalid",
      instructions: env.PJ_REALTIME_INSTRUCTIONS || DEFAULT_INSTRUCTIONS,
    };
  }

  const schemaUrl = deriveToolSchemasUrl(env);
  if (!schemaUrl) {
    return {
      tools: [],
      source: "disabled",
      detail: "PJ_TOOL_BRIDGE_URL (or PJ_TOOL_SCHEMAS_URL) is not configured",
    };
  }
  if (!(env.PJ_TOOL_BRIDGE_TOKEN || "").trim()) {
    return {
      tools: [],
      source: "misconfigured",
      detail: "PJ_TOOL_BRIDGE_TOKEN is not configured",
    };
  }
  if (isLoopTarget(schemaUrl, requestUrl)) {
    toolSchemaReconciliation = {
      status: "failed",
      attempted_at_ms: Date.now(),
    };
    return {
      tools: [],
      source: "misconfigured",
      detail: "Tool schema URL points to this same endpoint, creating a request loop",
    };
  }

  const now = Date.now();
  if (
    !forceRefresh
    && toolSchemaReconciliation.status === "success"
    && toolSchemaCache.fetched_at_ms
    && now - toolSchemaCache.fetched_at_ms < cacheTtlMs
  ) {
    return {
      tools: toolSchemaCache.tools,
      source: toolSchemaCache.source,
      detail: toolSchemaCache.detail,
      instructions: toolSchemaCache.instructions,
      instructions_sha256: toolSchemaCache.instructions_sha256,
      tool_manifest_sha256: toolSchemaCache.tool_manifest_sha256,
      prompt_perfecting_version: toolSchemaCache.prompt_perfecting_version,
      tool_policy_sha256: toolSchemaCache.tool_policy_sha256,
    };
  }

  let target;
  try {
    target = new URL(schemaUrl);
  } catch {
    toolSchemaReconciliation = {
      status: "failed",
      attempted_at_ms: Date.now(),
    };
    return {
      tools: [],
      source: "misconfigured",
      detail: "Tool schema URL is invalid",
    };
  }

  try {
    const resp = await fetchWithTimeout(
      target.toString(),
      {
        method: "GET",
        headers: {
          Accept: "application/json",
          ...bridgeHeaders(env, requestId),
        },
      },
      12000,
    );
    const raw = await resp.text();
    if (!resp.ok) {
      const detail = `status=${resp.status}; ${trimDetail(raw)}`;
      return bridgeSchemaFailure(detail);
    }
    let parsed = null;
    try {
      parsed = raw ? JSON.parse(raw) : null;
    } catch {
      parsed = null;
    }
    const tools = parseToolSchemaPayload(raw, maxTools);
    if (!parsed || parsed.contract_version !== CONTRACT_VERSION) {
      return bridgeSchemaFailure(
        "bridge contract version is missing or incompatible",
      );
    }
    const expectedToolManifestSha256 =
      typeof parsed.tool_manifest_sha256 === "string"
        ? parsed.tool_manifest_sha256
        : "";
    const actualToolManifestSha256 = Array.isArray(parsed.tools)
      ? await sha256Hex(stableJson(parsed.tools))
      : "";
    if (
      !/^[a-f0-9]{64}$/.test(expectedToolManifestSha256)
      || actualToolManifestSha256 !== expectedToolManifestSha256
    ) {
      return bridgeSchemaFailure(
        "bridge tool manifest failed SHA-256 validation",
      );
    }
    const instructions =
      parsed && typeof parsed.instructions === "string"
        ? parsed.instructions
        : "";
    const instructionsSha256 =
      parsed && typeof parsed.instructions_sha256 === "string"
        ? parsed.instructions_sha256
        : "";
    const actualInstructionsSha256 = instructions
      ? await sha256Hex(instructions)
      : "";
    if (
      !instructions.trim()
      || !/^[a-f0-9]{64}$/.test(instructionsSha256)
      || actualInstructionsSha256 !== instructionsSha256
    ) {
      return bridgeSchemaFailure(
        "bridge instructions are missing or failed SHA-256 validation",
      );
    }
    const promptPerfectingVersion =
      typeof parsed.prompt_perfecting_version === "string"
        ? parsed.prompt_perfecting_version.trim()
        : "";
    const toolPolicySha256 =
      typeof parsed.tool_policy_sha256 === "string"
        ? parsed.tool_policy_sha256
        : "";
    if (
      !promptPerfectingVersion
      || !/^[a-f0-9]{64}$/.test(toolPolicySha256)
    ) {
      return bridgeSchemaFailure(
        "bridge prompt or policy version metadata is invalid",
      );
    }
    toolSchemaCache = {
      tools,
      source: "bridge",
      detail: tools.length ? null : "bridge responded but no function tools were parsed",
      instructions,
      instructions_sha256: instructionsSha256,
      tool_manifest_sha256:
        actualToolManifestSha256,
      prompt_perfecting_version: promptPerfectingVersion,
      tool_policy_sha256: toolPolicySha256,
      fetched_at_ms: Date.now(),
    };
    toolSchemaReconciliation = {
      status: "success",
      attempted_at_ms: toolSchemaCache.fetched_at_ms,
    };
    return {
      tools: toolSchemaCache.tools,
      source: toolSchemaCache.source,
      detail: toolSchemaCache.detail,
      instructions: toolSchemaCache.instructions,
      instructions_sha256: toolSchemaCache.instructions_sha256,
      tool_manifest_sha256: toolSchemaCache.tool_manifest_sha256,
      prompt_perfecting_version: toolSchemaCache.prompt_perfecting_version,
      tool_policy_sha256: toolSchemaCache.tool_policy_sha256,
    };
  } catch (exc) {
    return bridgeSchemaFailure(trimDetail(exc), "bridge_unreachable");
  }
}

function normalizeSdp(sdpOffer) {
  const lines = String(sdpOffer || "").split(/\r?\n/).filter((line) => line.length > 0);
  if (!lines.length) {
    return "";
  }
  return `${lines.join("\r\n")}\r\n`;
}

async function requestRealtimeCall(
  sdpOffer,
  model,
  env,
  tools,
  voiceMode,
  instructions,
  fetchImpl = fetch,
) {
  const form = new FormData();
  form.set("sdp", sdpOffer);
  form.set(
    "session",
    JSON.stringify(
      createSessionConfig(model, env, tools, voiceMode, instructions),
    ),
  );

  try {
    const { response: openaiResp, text } = await fetchTextWithTimeout(
      "https://api.openai.com/v1/realtime/calls",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.OPENAI_API_KEY}`,
        },
        body: form,
      },
      REALTIME_CALL_TIMEOUT_MS,
      fetchImpl,
    );
    return {
      ok: openaiResp.ok,
      status: openaiResp.status,
      text,
      transportError: false,
    };
  } catch (exc) {
    return {
      ok: false,
      status: 502,
      text: `OpenAI realtime call request failed before completion: ${trimDetail(exc) || "unknown transport error"}`,
      transportError: true,
    };
  }
}

async function requestRealtimeClientSecret(
  model,
  env,
  tools,
  voiceMode,
  instructions,
  fetchImpl = fetch,
) {
  try {
    const { response: openaiResp, text } = await fetchTextWithTimeout(
      "https://api.openai.com/v1/realtime/client_secrets",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.OPENAI_API_KEY}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          session: createSessionConfig(
            model,
            env,
            tools,
            voiceMode,
            instructions,
          ),
        }),
      },
      REALTIME_TOKEN_TIMEOUT_MS,
      fetchImpl,
    );
    return {
      ok: openaiResp.ok,
      status: openaiResp.status,
      text,
      transportError: false,
    };
  } catch (exc) {
    return {
      ok: false,
      status: 502,
      text: `OpenAI client secret request failed before completion: ${trimDetail(exc) || "unknown transport error"}`,
      transportError: true,
    };
  }
}

function extractClientSecretPayload(rawText) {
  let parsed = null;
  try {
    parsed = rawText ? JSON.parse(rawText) : null;
  } catch {
    parsed = null;
  }
  const value = parsed?.client_secret?.value || parsed?.value || null;
  return {
    parsed,
    value,
  };
}

function shouldTryFallback(status, detail, primaryModel, fallbackModel) {
  if (!fallbackModel || fallbackModel === primaryModel || status !== 400) {
    return false;
  }
  const lowered = (detail || "").toLowerCase();
  return lowered.includes("model") || lowered.includes("unsupported");
}

function checkRequestTrust(request, allowedOrigins) {
  const origin = request.headers.get("origin");
  if (origin && !isAllowedOrigin(origin, allowedOrigins)) {
    return { ok: false, reason: `Origin not allowed: ${origin}` };
  }

  const refererOrigin = normalizedOriginFromReferer(request.headers.get("referer"));
  if (refererOrigin && !isAllowedOrigin(refererOrigin, allowedOrigins)) {
    return { ok: false, reason: `Referer origin not allowed: ${refererOrigin}` };
  }

  return { ok: true };
}

async function handleSession(request, env, corsOrigin, requestId) {
  if (!env.OPENAI_API_KEY) {
    return jsonResponse(
      errorPayload(
        "missing_openai_api_key",
        "OPENAI_API_KEY secret is not configured for this Worker.",
        requestId,
      ),
      500,
      corsOrigin,
      requestId,
    );
  }

  const contentType = request.headers.get("content-type") || "";
  if (contentType && !contentType.includes("application/sdp")) {
    return jsonResponse(
      errorPayload(
        "invalid_content_type",
        "Expected Content-Type: application/sdp for /session.",
        requestId,
        contentType,
      ),
      415,
      corsOrigin,
      requestId,
    );
  }
  const requestUrl = new URL(request.url);
  const extras = [...requestUrl.searchParams.keys()].filter(
    (key) => !["session_id", "voice_mode"].includes(key),
  );
  const sessionId = requestUrl.searchParams.get("session_id") || "";
  const voiceMode = requestUrl.searchParams.get("voice_mode") || "fast";
  if (
    extras.length ||
    !["fast", "full_power"].includes(voiceMode) ||
    (sessionId && !/^[A-Za-z0-9_-]{8,128}$/.test(sessionId))
  ) {
    return jsonResponse(
      errorPayload(
        "invalid_realtime_session",
        "session_id or voice_mode is invalid.",
        requestId,
      ),
      400,
      corsOrigin,
      requestId,
    );
  }

  const rawSdpOffer = await request.text();
  if (!rawSdpOffer.trim()) {
    return jsonResponse(
      errorPayload("missing_sdp_offer", "Missing SDP offer body.", requestId),
      400,
      corsOrigin,
      requestId,
    );
  }
  const sdpOffer = normalizeSdp(rawSdpOffer);
  if (!/^v=0/m.test(sdpOffer) || !/m=audio/m.test(sdpOffer)) {
    return jsonResponse(
      errorPayload(
        "invalid_sdp_offer",
        "SDP offer is malformed before forwarding to OpenAI.",
        requestId,
        `length=${sdpOffer.length}, sample=${trimDetail(sdpOffer.slice(0, 80))}`,
      ),
      400,
      corsOrigin,
      requestId,
    );
  }

  const primaryModel = (env.REALTIME_MODEL || DEFAULT_REALTIME_MODEL).trim();
  const fallbackModel = (env.REALTIME_MODEL_FALLBACK || FALLBACK_REALTIME_MODEL).trim();
  const toolBundle = await resolveRealtimeTools(env, requestId, request.url);
  const realtimeTools = toolBundle.tools;

  logEvent(requestId, "session.start", {
    primary_model: primaryModel,
    tool_count: realtimeTools.length,
    tool_source: toolBundle.source,
    tool_detail: toolBundle.detail,
  });
  let result = await requestRealtimeCall(
    sdpOffer,
    primaryModel,
    env,
    realtimeTools,
    voiceMode,
    toolBundle.instructions,
  );
  let selectedModel = primaryModel;

  if (shouldTryFallback(result.status, result.text, primaryModel, fallbackModel)) {
    logEvent(requestId, "session.retry_fallback", {
      from_model: primaryModel,
      to_model: fallbackModel,
    });
    result = await requestRealtimeCall(
      sdpOffer,
      fallbackModel,
      env,
      realtimeTools,
      voiceMode,
      toolBundle.instructions,
    );
    selectedModel = fallbackModel;
  }

  if (!result.ok) {
    logEvent(requestId, "session.upstream_error", {
      status: result.status,
      model: selectedModel,
      detail: trimDetail(result.text),
    });
    return jsonResponse(
      errorPayload(
        result.transportError ? "openai_realtime_unreachable" : "openai_realtime_failed",
        result.transportError
          ? "OpenAI realtime signaling was unreachable."
          : `Realtime signaling failed (model: ${selectedModel}).`,
        requestId,
        `sdp_length=${sdpOffer.length}; tool_count=${realtimeTools.length}; ${trimDetail(result.text)}`,
      ),
      result.status,
      corsOrigin,
      requestId,
    );
  }

  logEvent(requestId, "session.success", { model: selectedModel });
  const headers = responseHeaders(corsOrigin, requestId, "application/sdp");
  if (sessionId) headers["x-pj-session-id"] = sessionId;
  return new Response(result.text, {
    status: 200,
    headers,
  });
}

async function handleToken(request, env, corsOrigin, requestId) {
  if (!env.OPENAI_API_KEY) {
    return jsonResponse(
      errorPayload(
        "missing_openai_api_key",
        "OPENAI_API_KEY secret is not configured for this Worker.",
        requestId,
      ),
      500,
      corsOrigin,
      requestId,
    );
  }

  const rawBody = await request.text();
  let payload = {};
  if (rawBody.trim()) {
    try {
      payload = JSON.parse(rawBody);
    } catch {
      return jsonResponse(
        errorPayload("invalid_json", "Expected JSON body for /token when body is present.", requestId),
        400,
        corsOrigin,
        requestId,
      );
    }
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return jsonResponse(
      errorPayload(
        "invalid_realtime_session",
        "session_id or voice_mode is invalid.",
        requestId,
      ),
      400,
      corsOrigin,
      requestId,
    );
  }
  const extras = Object.keys(payload).filter(
    (key) => !["session_id", "voice_mode"].includes(key),
  );
  const sessionId = payload.session_id || "";
  const voiceMode = payload.voice_mode || "fast";
  if (
    extras.length ||
    !["fast", "full_power"].includes(voiceMode) ||
    (sessionId && !/^[A-Za-z0-9_-]{8,128}$/.test(sessionId))
  ) {
    return jsonResponse(
      errorPayload(
        "invalid_realtime_session",
        "session_id or voice_mode is invalid.",
        requestId,
      ),
      400,
      corsOrigin,
      requestId,
    );
  }

  const primaryModel = (env.REALTIME_MODEL || DEFAULT_REALTIME_MODEL).trim();
  const fallbackModel = (env.REALTIME_MODEL_FALLBACK || FALLBACK_REALTIME_MODEL).trim();
  const toolBundle = await resolveRealtimeTools(env, requestId, request.url);
  const realtimeTools = toolBundle.tools;

  logEvent(requestId, "token.start", {
    primary_model: primaryModel,
    tool_count: realtimeTools.length,
    tool_source: toolBundle.source,
    tool_detail: toolBundle.detail,
  });
  let result = await requestRealtimeClientSecret(
    primaryModel,
    env,
    realtimeTools,
    voiceMode,
    toolBundle.instructions,
  );
  let selectedModel = primaryModel;

  if (shouldTryFallback(result.status, result.text, primaryModel, fallbackModel)) {
    logEvent(requestId, "token.retry_fallback", {
      from_model: primaryModel,
      to_model: fallbackModel,
    });
    result = await requestRealtimeClientSecret(
      fallbackModel,
      env,
      realtimeTools,
      voiceMode,
      toolBundle.instructions,
    );
    selectedModel = fallbackModel;
  }

  if (!result.ok) {
    logEvent(requestId, "token.upstream_error", {
      status: result.status,
      model: selectedModel,
      detail: trimDetail(result.text),
    });
    return jsonResponse(
      errorPayload(
        result.transportError ? "openai_client_secret_unreachable" : "openai_client_secret_failed",
        result.transportError
          ? "OpenAI realtime client secret service was unreachable."
          : `Failed to create realtime client secret (model: ${selectedModel}).`,
        requestId,
        trimDetail(result.text),
      ),
      result.status,
      corsOrigin,
      requestId,
    );
  }

  const clientSecret = extractClientSecretPayload(result.text);
  if (!clientSecret.parsed || !clientSecret.value) {
    return jsonResponse(
      errorPayload(
        "invalid_client_secret_payload",
        "OpenAI returned an unexpected realtime client secret payload.",
        requestId,
        trimDetail(result.text),
      ),
      502,
      corsOrigin,
      requestId,
    );
  }

  logEvent(requestId, "token.success", { model: selectedModel });
  return jsonResponse(
    {
      ok: true,
      session_id: sessionId || null,
      model: selectedModel,
      tool_count: realtimeTools.length,
      client_secret: {
        value: clientSecret.value,
        expires_at: clientSecret.parsed?.client_secret?.expires_at || null,
      },
    },
    200,
    corsOrigin,
    requestId,
  );
}

async function handleToolSchemas(request, env, corsOrigin, requestId) {
  const forceRefresh = new URL(request.url).searchParams.get("refresh") === "1";
  const toolBundle = await resolveRealtimeTools(env, requestId, request.url, forceRefresh);
  return jsonResponse(
    {
      ok: true,
      count: toolBundle.tools.length,
      source: toolBundle.source,
      detail: toolBundle.detail,
      tools: toolBundle.tools,
      instructions_sha256: toolBundle.instructions_sha256 || null,
      tool_manifest_sha256: toolBundle.tool_manifest_sha256 || null,
      prompt_perfecting_version:
        toolBundle.prompt_perfecting_version || null,
      tool_policy_sha256: toolBundle.tool_policy_sha256 || null,
    },
    200,
    corsOrigin,
    requestId,
  );
}

async function handleExecuteTool(request, env, corsOrigin, requestId) {
  let payload = {};
  try {
    payload = await request.json();
  } catch {
    return jsonResponse(
      errorPayload("invalid_json", "Expected JSON body for /execute-tool.", requestId),
      400,
      corsOrigin,
      requestId,
    );
  }

  const bridgeUrl = (env.PJ_TOOL_BRIDGE_URL || "").trim();
  if (!bridgeUrl) {
    const name = payload?.name || null;
    const args = payload?.arguments || {};
    logEvent(requestId, "tool.unavailable", { name });
    return jsonResponse(
      {
        ...errorPayload(
          "edge_tools_unavailable",
          "Tool bridge is not configured. Set PJ_TOOL_BRIDGE_URL to an authenticated runtime endpoint.",
          requestId,
        ),
        tool: {
          name,
          arguments: args,
        },
      },
      501,
      corsOrigin,
      requestId,
    );
  }
  if (!(env.PJ_TOOL_BRIDGE_TOKEN || "").trim()) {
    return jsonResponse(
      errorPayload(
        "bridge_auth_not_configured",
        "The private tool bridge credential is not configured.",
        requestId,
      ),
      503,
      corsOrigin,
      requestId,
    );
  }

  let bridgeTarget;
  try {
    bridgeTarget = new URL(bridgeUrl);
  } catch {
    return jsonResponse(
      errorPayload(
        "invalid_bridge_url",
        "PJ_TOOL_BRIDGE_URL is invalid.",
        requestId,
        bridgeUrl,
      ),
      500,
      corsOrigin,
      requestId,
    );
  }
  if (isLoopTarget(bridgeTarget.toString(), request.url)) {
    return jsonResponse(
      errorPayload(
        "bridge_loop_detected",
        "PJ_TOOL_BRIDGE_URL points to this endpoint and would recurse.",
        requestId,
      ),
      500,
      corsOrigin,
      requestId,
    );
  }

  try {
    const bridgeResp = await fetchWithTimeout(
      bridgeTarget.toString(),
      { method: "POST", headers: bridgeHeaders(env, requestId), body: JSON.stringify(payload) },
      toolBridgeTimeoutMs(payload?.name),
    );
    const body = await bridgeResp.text();
    if (!bridgeResp.ok) {
      logEvent(requestId, "tool.bridge_error", {
        status: bridgeResp.status,
        detail: trimDetail(body),
      });
      return jsonResponse(
        errorPayload(
          "tool_bridge_failed",
          `Tool bridge request failed (${bridgeResp.status}).`,
          requestId,
          trimDetail(body),
        ),
        bridgeResp.status,
        corsOrigin,
        requestId,
      );
    }
    logEvent(requestId, "tool.bridge_success");
    return new Response(body || "{}", {
      status: 200,
      headers: responseHeaders(corsOrigin, requestId, "application/json"),
    });
  } catch (exc) {
    return jsonResponse(
      errorPayload(
        "tool_bridge_unreachable",
        "Tool bridge request failed before completion.",
        requestId,
        trimDetail(exc),
      ),
      502,
      corsOrigin,
      requestId,
    );
  }
}

export {
  CONTRACT_VERSION,
  bridgeHeaders,
  buildAccessConfig,
  createSessionConfig,
  deriveResponsesBridgeBaseUrl,
  fetchTextWithTimeout,
  handleSession,
  handleResponsesProxy,
  isPublicRoute,
  isResponsesRoute,
  responseHeaders,
  safeAttachmentDisposition,
  normalizeFunctionTools,
  resolveRealtimeTools,
  requestRealtimeCall,
  requestRealtimeClientSecret,
  toolBridgeTimeoutMs,
  stableJson,
  validateAccessClaims,
  validateAccessIdentity,
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const requestId = request.headers.get("x-pj-client-request-id") || crypto.randomUUID();
    const allowedOrigins = buildAllowedOrigins(env);
    const corsOrigin = pickCorsOrigin(request, allowedOrigins);

    if (!corsOrigin) {
      return jsonResponse(
        errorPayload("origin_not_allowed", "Origin is not allowed.", requestId),
        403,
        "null",
        requestId,
      );
    }

    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: responseHeaders(corsOrigin, requestId, "text/plain"),
      });
    }

    if (request.method === "GET" && url.pathname === "/health") {
      const bridgeConfigured = Boolean((env.PJ_TOOL_BRIDGE_URL || "").trim());
      const bridgeAuthConfigured = Boolean((env.PJ_TOOL_BRIDGE_TOKEN || "").trim());
      const resolvedTools = await resolveRealtimeTools(
        env,
        requestId,
        request.url,
      );
      const resolvedToolNames = new Set(
        resolvedTools.tools.map((tool) => tool.name),
      );
      const n8nToolsReady = [
        "list_n8n_capabilities",
        "get_n8n_corpus_status",
        "get_pj_capability_snapshot",
      ].every((name) => resolvedToolNames.has(name));
      return jsonResponse(
        {
          ok: true,
          worker: "pj-realtime-backend",
          contract_version: CONTRACT_VERSION,
          realtime_model: env.REALTIME_MODEL || DEFAULT_REALTIME_MODEL,
          tool_bridge_configured: bridgeConfigured,
          tool_bridge_auth_configured: bridgeAuthConfigured,
          tool_schema_cache_count: toolSchemaCache.tools.length,
          tool_schema_cache_source: toolSchemaCache.source,
          tool_manifest_sha256: toolSchemaCache.tool_manifest_sha256,
          instructions_sha256: toolSchemaCache.instructions_sha256,
          prompt_perfecting_version:
            toolSchemaCache.prompt_perfecting_version,
          tool_policy_sha256: toolSchemaCache.tool_policy_sha256,
          last_successful_reconciliation_at:
            toolSchemaCache.source === "bridge" && toolSchemaCache.fetched_at_ms
              ? new Date(toolSchemaCache.fetched_at_ms).toISOString()
              : null,
          tool_schema_reconciliation_status:
            toolSchemaReconciliation.status,
          last_reconciliation_attempt_at:
            toolSchemaReconciliation.attempted_at_ms
              ? new Date(
                toolSchemaReconciliation.attempted_at_ms,
              ).toISOString()
              : null,
          full_tooling_ready:
            bridgeConfigured
            && bridgeAuthConfigured
            && toolSchemaReconciliation.status === "success"
            && toolSchemaCache.source === "bridge"
            && toolSchemaCache.tools.length > 0
            && Boolean(toolSchemaCache.tool_manifest_sha256)
            && Boolean(toolSchemaCache.instructions_sha256)
            && Boolean(toolSchemaCache.prompt_perfecting_version)
            && Boolean(toolSchemaCache.tool_policy_sha256),
          n8n_corpus_tools_ready: n8nToolsReady,
          full_power_bridge_configured:
            Boolean(deriveResponsesBridgeBaseUrl(env)) && bridgeAuthConfigured,
          public_endpoints: ["GET /health", "OPTIONS *"],
          privileged_route_policy: "all other routes require an authorized Cloudflare Access identity",
          endpoints: [
            "/session",
            "/token",
            "/tool-schemas",
            "/execute-tool",
            "/responses/capabilities",
            "/responses/prompt-perfect",
            "/responses/sessions",
            "/responses/sessions/search",
            "/responses/sessions/<id>/resume",
            "/responses/sessions/<id>/turns",
            "/responses/sessions/<id>/realtime-messages",
            "/responses/sessions/<id>/artifacts",
            "/responses/sessions/<id>/approvals/<id>",
            "/responses/artifacts/<artifact-id>",
            "/health",
          ],
        },
        200,
        corsOrigin,
        requestId,
      );
    }

    const trust = checkRequestTrust(request, allowedOrigins);
    if (!trust.ok) {
      return jsonResponse(
        errorPayload("request_not_allowed", trust.reason, requestId),
        403,
        corsOrigin,
        requestId,
      );
    }

    if (!isPublicRoute(request.method, url.pathname)) {
      const access = await validateAccessIdentity(request, env);
      if (!access.ok) {
        logEvent(requestId, "access.denied", { code: access.code });
        return jsonResponse(
          errorPayload(access.code, access.message, requestId),
          access.status,
          corsOrigin,
          requestId,
        );
      }
    }

    if (request.method === "POST" && url.pathname === "/session") {
      return handleSession(request, env, corsOrigin, requestId);
    }

    if (request.method === "POST" && url.pathname === "/token") {
      return handleToken(request, env, corsOrigin, requestId);
    }

    if (request.method === "GET" && url.pathname === "/tool-schemas") {
      return handleToolSchemas(request, env, corsOrigin, requestId);
    }

    if (request.method === "POST" && url.pathname === "/execute-tool") {
      return handleExecuteTool(request, env, corsOrigin, requestId);
    }

    if (url.pathname.startsWith("/responses/")) {
      return handleResponsesProxy(request, env, corsOrigin, requestId);
    }

    return jsonResponse(
      errorPayload("not_found", "Not found.", requestId),
      404,
      corsOrigin,
      requestId,
    );
  },
};
