const CONTRACT_VERSION = "2026-07-28.4";
const DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1";
const FALLBACK_REALTIME_MODEL = "gpt-realtime";
const REALTIME_VOICE = "marin";
const MAX_ERROR_DETAIL_LENGTH = 320;
const DEFAULT_TOOL_SCHEMA_CACHE_TTL_MS = 60000;
const DEFAULT_MAX_REALTIME_TOOLS = 256;

let toolSchemaCache = {
  tools: [],
  source: "cold_start",
  detail: null,
  fetched_at_ms: 0,
};

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
    "access-control-expose-headers": "x-request-id,x-pj-contract-version",
  };
}

function responseHeaders(corsOrigin, requestId, contentType = "application/json") {
  return {
    "content-type": contentType,
    "cache-control": "no-store",
    "x-request-id": requestId,
    "x-pj-contract-version": CONTRACT_VERSION,
    ...corsHeaders(corsOrigin),
  };
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

async function fetchWithTimeout(url, options = {}, timeoutMs = 20000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new Error("timeout")), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

function createSessionConfig(model, env, tools) {
  return {
    type: "realtime",
    model,
    instructions: env.PJ_REALTIME_INSTRUCTIONS || DEFAULT_INSTRUCTIONS,
    audio: {
      input: {
        transcription: { model: "gpt-4o-transcribe" },
        turn_detection: {
          type: "server_vad",
          silence_duration_ms: 500,
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

function bridgeHeaders(env, requestId) {
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
  if (isLoopTarget(schemaUrl, requestUrl)) {
    return {
      tools: [],
      source: "misconfigured",
      detail: "Tool schema URL points to this same endpoint, creating a request loop",
    };
  }

  const now = Date.now();
  if (!forceRefresh && toolSchemaCache.fetched_at_ms && now - toolSchemaCache.fetched_at_ms < cacheTtlMs) {
    return {
      tools: toolSchemaCache.tools,
      source: toolSchemaCache.source,
      detail: toolSchemaCache.detail,
    };
  }

  let target;
  try {
    target = new URL(schemaUrl);
  } catch {
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
      return {
        tools: [],
        source: "bridge_error",
        detail,
      };
    }
    const tools = parseToolSchemaPayload(raw, maxTools);
    toolSchemaCache = {
      tools,
      source: "bridge",
      detail: tools.length ? null : "bridge responded but no function tools were parsed",
      fetched_at_ms: Date.now(),
    };
    return {
      tools: toolSchemaCache.tools,
      source: toolSchemaCache.source,
      detail: toolSchemaCache.detail,
    };
  } catch (exc) {
    return {
      tools: [],
      source: "bridge_unreachable",
      detail: trimDetail(exc),
    };
  }
}

function normalizeSdp(sdpOffer) {
  const lines = String(sdpOffer || "").split(/\r?\n/).filter((line) => line.length > 0);
  if (!lines.length) {
    return "";
  }
  return `${lines.join("\r\n")}\r\n`;
}

async function requestRealtimeCall(sdpOffer, model, env, tools) {
  const form = new FormData();
  form.set("sdp", sdpOffer);
  form.set("session", JSON.stringify(createSessionConfig(model, env, tools)));

  const openaiResp = await fetch("https://api.openai.com/v1/realtime/calls", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.OPENAI_API_KEY}`,
    },
    body: form,
  });

  const text = await openaiResp.text();
  return {
    ok: openaiResp.ok,
    status: openaiResp.status,
    text,
  };
}

async function requestRealtimeClientSecret(model, env, tools) {
  const openaiResp = await fetch("https://api.openai.com/v1/realtime/client_secrets", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.OPENAI_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      session: createSessionConfig(model, env, tools),
    }),
  });
  const text = await openaiResp.text();
  return {
    ok: openaiResp.ok,
    status: openaiResp.status,
    text,
  };
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
  let result = await requestRealtimeCall(sdpOffer, primaryModel, env, realtimeTools);
  let selectedModel = primaryModel;

  if (shouldTryFallback(result.status, result.text, primaryModel, fallbackModel)) {
    logEvent(requestId, "session.retry_fallback", {
      from_model: primaryModel,
      to_model: fallbackModel,
    });
    result = await requestRealtimeCall(sdpOffer, fallbackModel, env, realtimeTools);
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
        "openai_realtime_failed",
        `Realtime signaling failed (model: ${selectedModel}).`,
        requestId,
        `sdp_length=${sdpOffer.length}; tool_count=${realtimeTools.length}; ${trimDetail(result.text)}`,
      ),
      result.status,
      corsOrigin,
      requestId,
    );
  }

  logEvent(requestId, "session.success", { model: selectedModel });
  return new Response(result.text, {
    status: 200,
    headers: responseHeaders(corsOrigin, requestId, "application/sdp"),
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
  if (rawBody.trim()) {
    try {
      JSON.parse(rawBody);
    } catch {
      return jsonResponse(
        errorPayload("invalid_json", "Expected JSON body for /token when body is present.", requestId),
        400,
        corsOrigin,
        requestId,
      );
    }
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
  let result = await requestRealtimeClientSecret(primaryModel, env, realtimeTools);
  let selectedModel = primaryModel;

  if (shouldTryFallback(result.status, result.text, primaryModel, fallbackModel)) {
    logEvent(requestId, "token.retry_fallback", {
      from_model: primaryModel,
      to_model: fallbackModel,
    });
    result = await requestRealtimeClientSecret(fallbackModel, env, realtimeTools);
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
        "openai_client_secret_failed",
        `Failed to create realtime client secret (model: ${selectedModel}).`,
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
      20000,
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
      return jsonResponse(
        {
          ok: true,
          worker: "pj-realtime-backend",
          contract_version: CONTRACT_VERSION,
          realtime_model: env.REALTIME_MODEL || DEFAULT_REALTIME_MODEL,
          tool_bridge_configured: bridgeConfigured,
          tool_schema_cache_count: toolSchemaCache.tools.length,
          tool_schema_cache_source: toolSchemaCache.source,
          full_tooling_ready: bridgeConfigured && toolSchemaCache.tools.length > 0,
          endpoints: ["/session", "/token", "/tool-schemas", "/execute-tool", "/health"],
        },
        200,
        corsOrigin,
        requestId,
      );
    }

    if (
      (request.method === "POST" && (url.pathname === "/session" || url.pathname === "/token" || url.pathname === "/execute-tool")) ||
      (request.method === "GET" && url.pathname === "/tool-schemas")
    ) {
      const trust = checkRequestTrust(request, allowedOrigins);
      if (!trust.ok) {
        return jsonResponse(
          errorPayload("request_not_allowed", trust.reason, requestId),
          403,
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

    return jsonResponse(
      errorPayload("not_found", "Not found.", requestId),
      404,
      corsOrigin,
      requestId,
    );
  },
};
