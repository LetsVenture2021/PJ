/** Privacy-bounded browser continuity for PJ's zero-build ES-module client. */
export const SESSION_STORAGE_KEY = "pj.session.v1";
export const IDEMPOTENCY_HEADER = "x-pj-idempotency-key";

const ALLOWED = new Set(["conversationId", "eventCursor", "preferences", "uploads"]);
const ID = /^[A-Za-z0-9_-]{8,128}$/;

export function newIdempotencyKey(cryptoImpl = globalThis.crypto) {
  if (!cryptoImpl?.randomUUID) throw new Error("Secure random UUID support is required");
  return cryptoImpl.randomUUID();
}

export function backoffDelay(attempt, random = Math.random) {
  const bounded = Math.max(0, Math.min(Number(attempt) || 0, 6));
  return Math.round(Math.min(30_000, 500 * 2 ** bounded * (0.75 + random() * 0.5)));
}

export class SessionController {
  constructor(storage = globalThis.localStorage) {
    this.storage = storage;
    this.pending = new Map(); // deliberately memory-only: may contain request bodies
  }

  load() {
    try {
      const value = JSON.parse(this.storage?.getItem(SESSION_STORAGE_KEY) || "{}");
      return Object.fromEntries(Object.entries(value).filter(([key]) => ALLOWED.has(key)));
    } catch {
      return {};
    }
  }

  persist(next) {
    const safe = {};
    if (ID.test(next.conversationId || "")) safe.conversationId = next.conversationId;
    if (typeof next.eventCursor === "string" && next.eventCursor.length <= 256) {
      safe.eventCursor = next.eventCursor;
    }
    if (next.preferences && typeof next.preferences === "object")
      safe.preferences = next.preferences;
    if (Array.isArray(next.uploads)) {
      safe.uploads = next.uploads
        .map(({ uploadId, offset = 0, size = 0 }) => ({
          uploadId,
          offset: Math.max(0, Number(offset) || 0),
          size: Math.max(0, Number(size) || 0),
        }))
        .filter(({ uploadId }) => /^UPL-[a-f0-9]{32}$/.test(uploadId || ""));
    }
    this.storage?.setItem(SESSION_STORAGE_KEY, JSON.stringify(safe));
  }

  async submit(key, operation) {
    if (this.pending.has(key)) return this.pending.get(key);
    const request = Promise.resolve()
      .then(operation)
      .finally(() => this.pending.delete(key));
    this.pending.set(key, request);
    return request;
  }
}
