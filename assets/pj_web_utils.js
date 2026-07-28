export const CONTRACT_VERSION = "2026-07-28.6";

export function createRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  return `pj-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function shorten(value, max = 300) {
  if (!value) return "";
  const text = String(value).replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length <= max ? text : `${text.slice(0, max)}...`;
}

export function parseErrorBody(rawText) {
  if (!rawText) return "";
  if (/^\s*(?:<!doctype html|<html\b)/i.test(rawText)) {
    const title = rawText.match(/<title[^>]*>([^<]+)<\/title>/i)?.[1]
      ?.replace(/\s+/g, " ")
      .trim();
    return title
      ? `Server returned an HTML error page (${shorten(title, 160)})`
      : "Server returned an HTML error page";
  }
  try {
    const parsed = JSON.parse(rawText);
    const err = parsed.error || {};
    const parts = [];
    if (err.message) parts.push(err.message);
    if (err.detail) {
      if (typeof err.detail === "string") parts.push(err.detail);
      else parts.push(JSON.stringify(err.detail));
    }
    if (err.request_id) parts.push(`request_id=${err.request_id}`);
    if (parts.length) return shorten(parts.join(" | "));
  } catch (_) {}
  return shorten(rawText);
}

export async function fetchWithTimeout(url, options = {}, timeoutMs = 25000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(new Error("timeout")), timeoutMs);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}
