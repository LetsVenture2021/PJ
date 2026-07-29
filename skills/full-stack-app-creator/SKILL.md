---
name: full-stack-app-creator
description: Design, scaffold, implement, test, and document complete web applications across frontend, backend, database, authentication, APIs, and deployment configuration. Use when Codex needs to create a new full-stack app, turn a product idea or mockup into a working application, add an end-to-end feature spanning UI and server, choose an appropriate web stack, or bring an application from prototype quality to a production-ready baseline.
---

# Full Stack App Creator

Build a working application rather than a disconnected UI mockup or speculative architecture.

## Start with the repository

1. Read all applicable repository instructions before editing.
2. Inspect the existing stack, package manifests, routes, data models, tests, and deployment files.
3. Preserve established conventions and extend the current architecture when it is sound.
4. Check the working tree and avoid overwriting unrelated user changes.
5. Ask a question only when a missing product decision is truly blocking. Otherwise, state a
   reasonable assumption and proceed.

For a greenfield app, read [stack-selection.md](references/stack-selection.md) before choosing the
stack. For every task, use [delivery-checklist.md](references/delivery-checklist.md) as the final
review checklist.

## Define the product slice

Translate the request into a small, testable contract:

- Identify the primary user, core job, happy path, and important failure paths.
- List the screens, server operations, persistent entities, and external integrations involved.
- Separate the required first release from optional enhancements.
- Define acceptance criteria that can be verified through the rendered UI and automated tests.
- Prefer one complete vertical slice over many incomplete features.

When the request includes an image or mockup, inspect it closely and reproduce its visual hierarchy,
spacing, typography, color, and responsive behavior. Do not invent an unrelated design system.

## Plan the architecture

Choose the simplest architecture that meets the requirements:

1. Draw the request path from browser interaction through validation, business logic, persistence,
   and response rendering.
2. Define data ownership, relationships, constraints, and migration behavior before building forms.
3. Specify API or server-action contracts, including error shapes and authorization rules.
4. Keep secrets and privileged operations on the server. Expose only explicitly public
   configuration to the browser.
5. Avoid adding infrastructure, state libraries, queues, or services without a demonstrated need.

Follow the repository's existing patterns for modules and dependency injection. Keep domain logic
separate from transport and presentation code so it can be tested without a browser or network.

## Implement in vertical slices

Work from foundations toward the visible experience:

1. Add or update the schema and backward-compatible migrations.
2. Implement domain behavior and server-side validation.
3. Add the API, action, or route with explicit authentication and authorization.
4. Build the UI against the real contract, not hard-coded placeholder data.
5. Cover loading, empty, success, validation, permission, and unexpected-error states.
6. Add focused tests at each boundary before expanding the next slice.

Use transactions for multi-write invariants. Make retried mutations safe where practical. Return
useful user-facing errors without leaking stack traces, secrets, queries, or internal identifiers.

## Build a deliberate interface

- Establish a clear visual direction appropriate to the product instead of defaulting to generic
  cards and gradients.
- Reuse the app's design tokens and components; introduce new primitives only when reusable.
- Use semantic HTML, keyboard-operable controls, visible focus states, associated labels, and
  sufficient contrast.
- Design mobile and desktop layouts intentionally. Prevent overflow and preserve readable line
  lengths at intermediate widths.
- Keep animation purposeful and respect reduced-motion preferences.
- Do not leave nonfunctional buttons, fake metrics, placeholder navigation, or unexplained sample
  data in a claimed-complete flow.

If the environment permits it, run the app and inspect the changed experience in a browser. Capture
a screenshot when requested or when repository instructions require visual evidence.

## Secure every boundary

- Validate and normalize untrusted input on the server even when the client validates it.
- Enforce authorization on every protected read and mutation; never rely on hidden UI controls.
- Use framework-supported session, password, CSRF, cookie, and query-parameter mechanisms.
- Parameterize database access and escape output according to context.
- Apply upload size and type restrictions when accepting files.
- Keep credentials out of source control, logs, client bundles, fixtures, and screenshots.
- Minimize sensitive data collection and avoid logging request bodies or personal content.
- Check dependencies and generated configuration for unsafe defaults.

## Verify the result

Run the repository-prescribed formatter, linter, type checker, unit tests, integration tests, and
production build. Add browser or end-to-end coverage for the primary flow when the project supports
it. Do not replace meaningful assertions with snapshots alone.

Test at least:

- the domain happy path and an invalid-input path;
- unauthenticated and unauthorized behavior where applicable;
- persistence constraints and rollback behavior;
- the UI's loading, empty, success, and error states;
- a narrow and a wide viewport for perceptible UI changes;
- build-time validation of environment configuration.

Treat warnings as findings: fix those caused by the change and clearly distinguish genuine
environment limitations from code failures.

## Finish cleanly

Review the diff for accidental generated files, secrets, debugging output, dead code, and unrelated
formatting. Update concise setup or operator documentation only when behavior or configuration
changed. Summarize what works, identify assumptions, list exact verification commands and results,
and call out any remaining limitation honestly.
