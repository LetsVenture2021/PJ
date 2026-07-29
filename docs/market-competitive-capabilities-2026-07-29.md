# Market-competitive capability gaps

**Date:** July 29, 2026

**Purpose:** Identify the additional product capabilities PJ needs to compete
with leading general-purpose AI assistants and work copilots.

## Scope and interpretation

This is a product-planning assessment, not a claim that every competitor ships
every feature in the same form. The comparison uses durable market categories:
multimodal assistants, research assistants, enterprise copilots, agentic work
platforms, and creative workspaces. Competitor packaging changes quickly, so a
fresh feature and pricing check should precede roadmap investment.

PJ already has a strong technical base: text and voice interaction, local
tools, uploads, saved chats, document and presentation generation, web and file
search, MCP connectors, approvals, spend controls, and local-first state. The
gaps below are the capabilities needed to turn that foundation into a complete,
competitive product experience.

## Recommended capabilities, in priority order

### P0 — Required to be credible

1. **Instant, unified chat experience**
   - Make text usable immediately, without starting a session or choosing a
     runtime mode.
   - Use one composer for text, voice, files, camera, and pasted content.
   - Route requests automatically by latency, capability, cost, and risk while
     retaining an expert override.
   - Add a useful empty state, example outcomes, onboarding, and plain-language
     recovery messages.

   **Why it matters:** Customers compare the first minute, not the tool count.
   PJ's current mode and connection ceremony makes a routine chat interaction
   feel like infrastructure administration.

2. **Cross-device, responsive product surface**
   - Deliver a mobile-quality progressive web application with synchronized
     chats, projects, uploads, artifacts, and approvals.
   - Support push-to-talk, background-safe upload, reconnect, and a handoff
     between voice and desktop work.
   - Provide keyboard-complete interaction, captions, screen-reader status
     announcements, visible focus, and reduced-motion support.

   **Why it matters:** A personal assistant must be available where work starts,
   not only on the host computer or a desktop browser.

3. **Projects and shared working context**
   - Let conversations graduate into projects containing instructions, sources,
     people, goals, plans, tasks, decisions, budgets, and artifacts.
   - Scope retrieval, memory, connectors, and permissions to a project.
   - Add project templates and a portable export/import package.

   **Why it matters:** Saved chat history is insufficient for multi-day work.
   Competing work assistants organize persistent context around an outcome, not
   merely a transcript.

4. **Inspectable, user-controlled memory**
   - Propose memories with source, scope, confidence, and expiration rather than
     silently storing inferred facts.
   - Allow the owner to approve, edit, pin, forget, bulk delete, export, and
     disable categories of memory.
   - Show which memories influenced each response and prevent leakage between
     projects.

   **Why it matters:** Continuity is a baseline expectation, while inspectability
   and provenance can differentiate PJ's local-first approach.

5. **Research mode with evidence management**
   - Plan multi-step research, search across web, local files, and connectors,
     and let the user refine the plan before execution.
   - Produce claim-level citations, a source table, conflict detection,
     freshness dates, coverage gaps, and a reusable research bundle.
   - Add citation-entailment and broken-link checks before marking work
     verified.

   **Why it matters:** Web search alone does not match dedicated research
   experiences. Trust depends on connecting each consequential claim to current
   evidence.

6. **Durable background jobs**
   - Run long tasks asynchronously with pause, resume, cancel, retry, scheduling,
     progress, checkpoints, and reconnect-safe status.
   - Guarantee idempotent side effects and resume after runtime or browser
     failure without repeating completed actions.
   - Notify the owner only when a decision, failure, or finished outcome needs
     attention.

   **Why it matters:** Agentic workflows cannot depend on an open browser tab or
   one uninterrupted model turn.

7. **Outcome and artifact workspace**
   - Present results as outcome cards containing deliverable, evidence, changes,
     cost, elapsed time, uncertainty, and next actions.
   - Provide native preview, targeted editing, comments, version comparison,
     branching, restoration, and export for documents, decks, sheets, images,
     and code.
   - Validate format-specific quality, such as formula reconciliation, slide
     overflow, citation support, link health, and declared code checks.

   **Why it matters:** Generated files are table stakes; customers retain a
   product that helps them finish, inspect, and revise usable deliverables.

### P1 — Required to win repeat use

8. **Broad connector and action ecosystem**
   - Add guided OAuth connections for email, calendar, cloud drives, team chat,
     issue trackers, CRM, notes, and common business suites.
   - Publish connector health, granted scopes, last use, data residency,
     revocation, and least-privilege guidance.
   - Define preview, approval, idempotency, receipt, and rollback contracts for
     every action-capable connector.

   **Why it matters:** MCP support is valuable infrastructure, but customers
   expect polished connections and reliable actions rather than manual server
   configuration.

9. **Calendar, email, and meeting intelligence**
   - Prepare daily priorities, meeting briefs, agenda suggestions, and follow-up
     drafts using explicitly connected sources.
   - Transcribe meetings with speaker labeling, consent indicators, decisions,
     action items, and links back to supporting moments.
   - Keep sending, invitations, and external commitments behind preview and
     approval.

   **Why it matters:** These are high-frequency assistant workflows that create
   recurring value and make personal context useful.

10. **Multimodal understanding and live assistance**
    - Add camera and screen-share understanding, image and PDF region citation,
      OCR correction, diagram/chart interpretation, and visual grounding.
    - Allow live voice to refer to the current screen or camera while preserving
      a text transcript and accessibility equivalent.
    - Make capture boundaries obvious and never retain audio, video, or screen
      content beyond the declared policy.

    **Why it matters:** Voice without visual context is increasingly incomplete
    for troubleshooting, learning, and mobile assistance.

11. **Reusable assistants and workflow builder**
    - Let owners package instructions, tools, knowledge, output schemas, budgets,
      and approval policies into reusable assistants or recipes.
    - Offer a no-code builder, test mode, versioning, import/export, and a run
      history with evaluation results.
    - Keep generated code and community imports sandboxed and approval-gated.

    **Why it matters:** PJ has generated skills, but repeatable workflows need a
    customer-facing lifecycle rather than a developer-only mechanism.

12. **Scheduled and event-triggered automation**
    - Support schedules and verified connector events with simulation, dry run,
      rate limits, budgets, quiet hours, and a global kill switch.
    - Separate suggestions from autonomous actions and require stronger policy
      for destructive, public, credentialed, or paid effects.

    **Why it matters:** Proactivity drives retention only when it remains
    predictable, low-noise, and reversible.

13. **Personalization and model routing controls**
    - Learn only owner-approved preferences for tone, formats, sources, and
      recurring workflows.
    - Offer understandable modes such as quick, balanced, deep, and local/private
      rather than exposing provider internals.
    - Show estimates and actuals for cost, latency, and significant tool usage.

    **Why it matters:** Automatic routing improves simplicity, but expert users
    still need control and cost transparency.

### P2 — Needed for expansion beyond the current owner-only product

14. **Collaboration and controlled sharing**
    - Share a specific chat, source set, artifact, or project with viewer,
      commenter, and editor roles plus expiration and revocation.
    - Add comments, mentions, change attribution, approval routing, and external
      link controls.
    - Prevent personal memory and unrelated project context from entering a
      shared space.

    **Why it matters:** Collaboration is expected in workplace products, but it
    should be added only through an explicit multi-user security model.

15. **Organization administration and compliance**
    - Add tenant isolation, SSO/SCIM, role-based access, connector allowlists,
      retention policies, legal hold/export, regional controls, and administrator
      audit views.
    - Provide policy versioning, evaluation evidence, incident controls, and
      auditable data deletion.

    **Why it matters:** These are entry requirements for organizational sales,
    not incremental settings for an owner-only runtime.

16. **Managed deployment, sync, and recovery**
    - Replace ad hoc multi-surface deployment with a signed release manifest,
      staged promotion, drift detection, rollback, and an operator dashboard.
    - Add encrypted multi-device synchronization, migrations, conflict handling,
      backup verification, and guided restore.
    - Preserve a local-only mode and make cloud boundaries explicit.

    **Why it matters:** PJ cannot promise cross-device or team reliability while
    state remains unmigrated and tied to one machine.

17. **Extension marketplace and developer platform**
    - Publish versioned tool, event, artifact, and policy contracts with a local
      test harness and compatibility checks.
    - Add signed packages, permission review, provenance, revocation, staged
      rollout, and security reporting.
    - Curate templates before allowing an open marketplace.

    **Why it matters:** An ecosystem expands coverage faster than first-party
    development, but ungoverned extensions would undermine PJ's central trust
    advantage.

## Competitive position to preserve

PJ should not chase feature parity by weakening its strongest differentiators:

- **Local-first ownership:** durable personal data can remain on infrastructure
  the owner controls.
- **Risk-proportionate approvals:** consequential, paid, credentialed, and
  destructive actions stay explicitly governed.
- **Metadata-only observability:** product measurement must not require storing
  prompts, tool arguments, results, request bodies, or credentials.
- **Verifiable artifacts and actions:** provenance, integrity, idempotency, and
  receipts should become visible product features rather than hidden plumbing.
- **Interoperability:** MCP and portable project/artifact exports should prevent
  lock-in.

## Suggested delivery sequence

### Release 1: remove adoption friction

Ship unified instant text, automatic routing, first-run guidance, responsive
accessibility, plain-language errors, and privacy-safe journey metrics. Target a
median time to first message below 30 seconds for a new user and at least 90%
first-attempt task start in usability testing.

### Release 2: create a durable work product

Ship projects, inspectable memory, research bundles, outcome cards, and the
artifact workspace. Measure project return rate, citation entailment, artifact
first-pass acceptance, and verified outcomes per active owner hour.

### Release 3: make execution dependable

Ship background jobs, connector management, action contracts, scheduling, and
automation simulation. Gate release on restart recovery, cancellation, budget,
approval, and duplicate-side-effect fault-injection tests.

### Release 4: expand the market deliberately

Choose either a polished personal multi-device product or an organization-ready
collaboration product before building both. The latter requires tenant identity,
isolation, administration, retention, and audit architecture; it should not be
layered casually onto the current owner-only design.

## Build-versus-defer rule

Prioritize a capability only if it improves one of four measurable outcomes:

1. faster arrival at a useful result;
2. higher completion and verification quality;
3. safer execution of real work; or
4. stronger repeat use through durable context.

Defer features that add catalog breadth without improving an end-to-end task.
In particular, do not prioritize a public assistant marketplace, unrestricted
autonomous browsing, social feeds, avatar novelty, or silent memory capture
before the P0 reliability, usability, and control gaps are closed.
