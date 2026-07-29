# PJ specialist agents

PJ includes 15 repository-scoped Codex agent roles in `.codex/agents/`. They are
designed for bounded delegation: each role owns a distinct decision surface,
states its non-negotiable constraints, and returns evidence that the primary
agent can verify.

## Catalog

| Agent | Best used for | Access posture |
| --- | --- | --- |
| `architecture_guardian` | Domain boundaries and compatibility design | Read only |
| `security_guardian` | Threat modeling and security review | Read only |
| `responses_engineer` | Responses orchestration implementation | Workspace write |
| `realtime_engineer` | Realtime, voice, and WebRTC implementation | Workspace write |
| `edge_engineer` | Worker routes, authentication, and CORS | Workspace write |
| `test_strategist` | Deterministic regression coverage | Workspace write |
| `python_quality` | Python correctness and focused refactoring | Workspace write |
| `state_steward` | SQLite, continuation, and artifact-state design | Read only |
| `document_specialist` | Safe ingestion and artifact workflows | Workspace write |
| `experience_reviewer` | UX and accessibility review | Read only |
| `reliability_engineer` | Failure handling and recovery implementation | Workspace write |
| `release_captain` | Release gates and readiness assessment | Read only |
| `product_strategist` | Prioritization and outcome planning | Read only |
| `research_analyst` | Source-grounded research | Read only |
| `red_team` | Independent adversarial review | Read only |

The project configuration caps concurrent work at four threads and delegation
depth at one. This keeps orchestration legible and prevents specialists from
creating unbounded delegation trees.

## Operating pattern

1. Give one agent a concrete, bounded output with explicit acceptance criteria.
2. Use implementation agents only for disjoint file ownership; agents share the
   working tree, so overlapping edits create avoidable risk.
3. Pair consequential implementation with `security_guardian` or `red_team`,
   but keep the reviewer independent from the author.
4. Treat every agent result as advisory until the primary agent inspects the
   diff and runs the repository's required checks.
5. Keep credentials, prompts, tool arguments, and results out of delegation
   messages and logs.

Agents inherit the session's model unless their configuration says otherwise.
The role files set reasoning effort and permissions but deliberately avoid a
hard-coded model identifier so repository configuration remains authoritative.
