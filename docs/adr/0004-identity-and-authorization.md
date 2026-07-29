# ADR 0004: Identity and authorization

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

PJ has terminal, loopback Flask, Worker, provider, connector, and automation
principals. Authentication at the edge alone is insufficient for object-level
authorization.

## Decision

Normalize verified credentials into a typed principal containing issuer,
subject, tenant, actor type, authentication strength, and bounded roles. Trust
only the terminal owner context, loopback owner session, verified Cloudflare
Access assertion, or authenticated internal bridge. The Worker replaces rather
than forwards identity and authorization headers.

Every protected operation authorizes a tuple of principal, action, resource,
tenant/project scope, capability flag, and request context. Default is deny.
Object ownership, approval-sensitive actions, paid actions, connector scopes,
and automation each receive explicit policy rules. Tools cannot self-assert
approval. Decisions emit metadata-only telemetry with policy/rule identifiers
and allow/deny, never credentials or request contents.

## Consequences

Routes, tool dispatch, schedules, artifact downloads, and connector actions
share the same policy vocabulary. Release evidence includes an authorization
matrix with unauthenticated, wrong-tenant, wrong-project, expired, replayed,
and insufficient-role cases.
