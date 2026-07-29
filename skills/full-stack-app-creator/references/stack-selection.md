# Stack selection

Use this guide only for greenfield applications or when the user explicitly requests a stack
decision. In an existing repository, prefer its supported stack.

## Decision order

1. Honor explicit user constraints and the target hosting environment.
2. Prefer tools already available in the repository or execution environment.
3. Choose mature, maintained components with strong typing, migrations, testing, and security
   support.
4. Optimize for the smallest operational footprint that meets the product requirements.
5. Record consequential tradeoffs in existing project documentation rather than adding an
   unnecessary architecture document.

## Architecture choices

### Integrated web framework

Prefer a framework with server rendering, routing, forms or server actions, and API support when one
team owns the product and independent scaling is not required. This usually minimizes duplicated
types, deployment units, authentication boundaries, and client-side state.

### Separate frontend and API

Use separate applications when the API serves multiple clients, release cycles must be independent,
or the existing platform already enforces that boundary. Define a versioned contract and generate or
share types when practical. Configure CORS narrowly and plan authentication across origins.

### Relational database

Default to a relational database for accounts, permissions, transactions, workflows, and data with
meaningful constraints. Use the framework's migration tooling. SQLite is suitable for local-first or
single-instance products when its concurrency and deployment constraints are acceptable; use a
managed client-server database for coordinated multi-instance writes.

### Document or key-value storage

Choose non-relational storage only when access patterns and consistency needs justify it. Do not use
schema flexibility as a substitute for modeling. Keep validation and versioning explicit.

### Background work

Keep fast, reliable operations in the request path. Add a durable queue only for work that is slow,
retryable, scheduled, or independently scalable. Define idempotency, retry limits, observability,
and failure recovery before introducing workers.

## Dependency filter

Before adding a dependency, answer:

- Does the platform or current framework already solve this?
- Is the package maintained and compatible with the required runtime?
- What client-bundle, security, licensing, and operational cost does it add?
- Can the behavior be tested without a live third-party service?
- Is removal or replacement straightforward?

Avoid selecting versions from memory when current information matters. Use lockfiles and official
documentation, and verify compatibility with the actual runtime.
