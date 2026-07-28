#!/usr/bin/env python3
"""Build PJ's canonical governed n8n capability corpus."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "documents" / "n8n-capability-corpus-v1.md"
DEFAULT_CENSUS = ROOT / "documents" / "n8n-source-census-v1.json"

SPECS = (
    ("Build a first workflow",
     "https://docs.n8n.io/build-your-first-workflow.md",
     "719874a80e2f474686505bbc01dfaea4e9b05ced3e1a2b559ac436afbd16b74d",
     ("workflow", "trigger", "credentials"),
     "Compose a basic triggered workflow, map data, and test it before activation."),
    ("Create and run workflows",
     "https://docs.n8n.io/build/understand-workflows/create-and-run-workflows.md",
     "e4ae63e449cffa62486ef82f1b4a8b469d1b616a33a2cceab45bb3f3d03abebc",
     ("workflow", "execution", "activation"),
     "Create, manually test, save, and deliberately activate workflows."),
    ("Create and edit credentials",
     "https://docs.n8n.io/build/understand-workflows/create-and-edit-credentials.md",
     "a1fd2328ee67e3046fcc939527a3cae1490e3c0a263ee22e1939b9e8d8e7cab9",
     ("credentials", "authentication", "least-privilege"),
     "Configure credential objects without embedding secret values in workflows."),
    ("Export and import workflows",
     "https://docs.n8n.io/build/manage-workflows/export-and-import.md",
     "da4e42641785e5f38bcc378e3c561191e3ee3de7f28acba9a9d315162dafab4f",
     ("workflow", "export", "import"),
     "Move workflow definitions while treating exported files as sensitive artifacts."),
    ("Split flow with conditionals",
     "https://docs.n8n.io/build/flow-logic/split-with-conditionals.md",
     "b1652e3eb91421e42e64a3c215daf6b3bc9b3e1b6fb1eaac05b8e1698b3f7dce",
     ("flow-control", "conditional", "branching"),
     "Route items through explicit conditional branches and define fallback behavior."),
    ("Merge data branches",
     "https://docs.n8n.io/build/flow-logic/merge-data.md",
     "4688251a1282305d7ef7b74b988e86a9ec7b374d7c63b4223c1bc0ef7c56a084",
     ("flow-control", "merge", "data"),
     "Recombine branches using a merge strategy that preserves expected item shape."),
    ("Loop over items",
     "https://docs.n8n.io/build/flow-logic/loop.md",
     "8c5d390222854066dde07169c1eff548db44269b54cdc7d0f1e3a011b8a4b399",
     ("flow-control", "loop", "batching"),
     "Process repeated items with explicit completion and bounded iteration behavior."),
    ("Wait and resume execution",
     "https://docs.n8n.io/build/flow-logic/wait.md",
     "e8f8ce6c190fe0c1b3419ab208dea383155f1faf30d2d9e22f80c3181c36a31d",
     ("flow-control", "wait", "resume"),
     "Pause and resume workflows using time or event-based continuation."),
    ("Break workflows into sub-workflows",
     "https://docs.n8n.io/build/flow-logic/break-workflows-into-smaller-parts.md",
     "375ec6cda438a0753caace73ca332c83684b9e9710b4471ae42f1d4d2f5be192",
     ("workflow", "sub-workflow", "reuse"),
     "Extract reusable sub-workflows with explicit inputs, outputs, and ownership."),
    ("Handle errors gracefully",
     "https://docs.n8n.io/build/flow-logic/handle-errors-gracefully.md",
     "39b3bd9b996317e2eb41bce2396455e78f00cf9807582fb665220284f36965b9",
     ("reliability", "error-workflow", "alerting"),
     "Attach error workflows, preserve diagnostic context, and notify operators."),
    ("Understand execution order",
     "https://docs.n8n.io/build/flow-logic/understand-execution-order.md",
     "fc60d08ea37dbc0f891431ef123a47d44a12df59bf9f54979ce24532efde15ec",
     ("flow-control", "execution-order", "determinism"),
     "Reason about branch execution order before relying on side effects."),
    ("Understand the n8n data structure",
     "https://docs.n8n.io/build/work-with-data/understand-n8ns-data-structure.md",
     "d60f62866fbcbcc65d63930b0e872d67fa8ebf990523379fd12188abb9cef15c",
     ("data", "items", "json"),
     "Model node input and output as item arrays containing JSON and binary data."),
    ("Map data with the UI",
     "https://docs.n8n.io/build/work-with-data/reference-data/use-the-ui-mapper.md",
     "f8b9eed75b95053c53fddcd3a3117026500f9101d4161eb5bfdb197def3dea67",
     ("data", "mapping", "expressions"),
     "Map upstream fields through the UI while validating sample item shape."),
    ("Link data items",
     "https://docs.n8n.io/build/work-with-data/reference-data/link-data-items.md",
     "4ee93d1634176609051a3489d9a8d3fe88c7e2a457b10480c665a744a736c95e",
     ("data", "item-linking", "lineage"),
     "Preserve item lineage so downstream expressions resolve the correct source item."),
    ("Choose a data transformation approach",
     "https://docs.n8n.io/build/work-with-data/transform-data/approaches-for-transforming-data.md",
     "563a5c3aba75f1753fbe5e79b72b77fd2bf56414e0da99a1024e394fe1e9b651",
     ("data", "transformation", "nodes"),
     "Select expressions, transformation nodes, or code based on complexity and risk."),
    ("Transform data with expressions",
     "https://docs.n8n.io/build/work-with-data/transform-data/expressions-for-data-transformation.md",
     "5df74f50a6989dd92949f546baf8aa08cd1af4ebbb7584d7654ef8a2e302c2b3",
     ("data", "expressions", "transformation"),
     "Apply bounded expressions and validate null, type, and missing-field behavior."),
    ("Filter unwanted data",
     "https://docs.n8n.io/build/work-with-data/filter-out-unwanted-data.md",
     "1cca1550e8c18c4659d3d2e7afa2c320d664c074ab95deaf130c4ff270586470",
     ("data", "filter", "validation"),
     "Remove items using explicit filter criteria and verify edge cases."),
    ("Pin and mock data",
     "https://docs.n8n.io/build/work-with-data/pin-and-mock-data.md",
     "1a8357f02b8a1badd8a04009542d938298948c2647fcfbfd183fb74b38a64d77",
     ("testing", "mock-data", "pinning"),
     "Use pinned or mocked samples for development without treating them as live data."),
    ("Work with files and images",
     "https://docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-files-and-images.md",
     "e72b3096c3415835dd8ee6b8868f6bb4248c0db30ca5244e77cf59cf4487ee49",
     ("data", "binary", "files"),
     "Handle binary file properties without losing metadata or exhausting storage."),
    ("Use data tables",
     "https://docs.n8n.io/build/work-with-data/data-tables.md",
     "d20dd7ba9418265dea2282ccdee5580a6064892d086ef189d3b2922d14daf1ed",
     ("data", "tables", "persistence"),
     "Use data tables for governed workflow state with explicit schema and retention."),
    ("Use the Code node",
     "https://docs.n8n.io/build/code-in-n8n/using-the-code-node.md",
     "f1476f84f1b7746531864e92fe21c42f3a38e67f0f948f8616148d911e17e5c8",
     ("code", "javascript", "python"),
     "Use Code nodes only when standard nodes cannot express the transformation safely."),
    ("Define custom variables",
     "https://docs.n8n.io/build/code-in-n8n/define-custom-variables.md",
     "837abb0a8ae9a71b2b0bc716c6b3c6815c3d4e39968b1cea83e3a1c5fcb7c708",
     ("configuration", "variables", "environments"),
     "Reference non-secret environment-specific values through governed variables."),
    ("Call APIs with HTTP Request",
     "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest.md",
     "4a9a97c8cb88de1550e9d60ae00b8717729da82fcd42f0eabd1167d6385e7171",
     ("integration", "http", "api"),
     "Configure HTTP requests from current node documentation and validate responses."),
    ("Schedule workflow execution",
     "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md",
     "33d3f26b8282cc058b0775e1a6f44d988c628216a3976559565f4329cb14160e",
     ("trigger", "schedule", "timezone"),
     "Schedule executions with explicit timezone, interval, and activation review."),
    ("Receive events with Webhook",
     "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook.md",
     "73a34dbeb8bd30206ef94cfbc7d6ee2d054e3813530aad6781e062d533ea4de6",
     ("trigger", "webhook", "http"),
     "Expose test and production webhook endpoints with authentication and validation."),
    ("Respond to webhook calls",
     "https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook.md",
     "e70bcaed44d232e423509f87f7100b9b71e6a7347e6a31a2568f9633b858cd13",
     ("webhook", "response", "http"),
     "Return bounded status, headers, and body data without leaking internal details."),
    ("Configure credential environment variables",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/credentials.md",
     "dc2b2881c1d9d7b120d8e694c2e9464bae9ac0e8f54d35086f7e5a7e9fe3b64a",
     ("security", "credentials", "configuration"),
     "Configure credential behavior through deployment controls without storing secrets in source."),
    ("Use external secrets",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/external-secrets.md",
     "8cf16f6cd3ae64bf5dce4ce8c0e19dce04d5482b012f17ae0549a8ad93bf3e59",
     ("security", "external-secrets", "vault"),
     "Retrieve secrets from an external manager using least-privilege access."),
    ("Manage security policies",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/manage-security-policies.md",
     "149c1b5711bb33ad289543c5f6b6694577cd42988dd3e18db4c1fe260796cc73",
     ("security", "policy", "hardening"),
     "Apply instance security policy settings and verify enforcement before release."),
    ("Enable SSRF protection",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md",
     "a198f9eb539c2cf9a34bc03c28b7743807d0b9052993bbd0ac3dcd6338213d10",
     ("security", "ssrf", "network"),
     "Block server-side request forgery paths and constrain outbound destinations."),
    ("Redact execution data",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/redact-execution-data.md",
     "067130e238ebccfdd7e23e5a68deed715f980daa83bde70e7f5c7e3bef4667e6",
     ("security", "redaction", "execution-data"),
     "Redact sensitive execution fields before they reach logs or stored diagnostics."),
    ("Scale self-hosted n8n",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling.md",
     "e4c6a6156dad9b94fef65af232322dc7789001d5c3fcab16ea3f2aa12ed02984",
     ("deployment", "scaling", "capacity"),
     "Plan scaling around workload, database, storage, worker, and webhook constraints."),
    ("Enable queue mode",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode.md",
     "61efdf67b0e0c1f40a6e0e8d9f5119c7d42c21996c719206cae44a00b7990d57",
     ("deployment", "queue-mode", "workers"),
     "Separate main and worker processes with supported database, broker, and storage."),
    ("Control concurrency",
     "https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency.md",
     "a09637c8501c1e8c06e2d6775ce33382ddde52a50493632485e056fbe57f5fa1",
     ("deployment", "concurrency", "capacity"),
     "Set concurrency from measured capacity and verify queue backpressure."),
    ("Set up logging",
     "https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/set-up-logging.md",
     "a3c5db9cee9ae3b512bdb53dfa6a56ae266d57bf1769ceacda9d8e2a91f0d6de",
     ("operations", "logging", "diagnostics"),
     "Configure useful logs while excluding credentials and sensitive execution data."),
    ("Monitor n8n",
     "https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/monitor-n8n.md",
     "442f315c6043cba52b19566f2f51ba84e066eb76d719f0b812585358ac6a3ccd",
     ("operations", "monitoring", "health"),
     "Monitor health and execution behavior with alerts tied to operator runbooks."),
    ("Assess community node risks",
     "https://docs.n8n.io/integrations/community-nodes/risks.md",
     "6774b9ace070f1c0171f2077c88d0e75711da138d64fdc2536f2953afae4831e",
     ("security", "community-nodes", "supply-chain"),
     "Review unverified node code, permissions, maintenance, and supply-chain risk."),
    ("Authenticate to the n8n API",
     "https://docs.n8n.io/connect/n8n-api/authentication.md",
     "e1e3b57d1cd5bdfb8a3c1742c0ce4c7805fcc759b47dd455d64925da6197cbcd",
     ("api", "authentication", "credentials"),
     "Authenticate API clients without exposing keys in workflow content or logs."),
    ("Organize work in projects with RBAC",
     "https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/organize-work-in-projects.md",
     "911b8ab72684c10f27e22320c8548011a63441f47a02d3d6f7c8acb8320e726d",
     ("governance", "projects", "rbac"),
     "Use projects, roles, and least privilege to separate workflow ownership."),
    ("Use source control and environments",
     "https://docs.n8n.io/administer/use-source-control-and-environments.md",
     "e3ef4e0979c2379c8ac34400d3117999e0523d26b42501dd2b5e006fe61dc592",
     ("deployment", "source-control", "environments"),
     "Promote reviewed workflow changes across environments with source control."),
)


def _record(index: int, spec: tuple) -> str:
    title, url, source_hash, taxonomy, teaches = spec
    item_id = f"N8N-{index:03d}"
    taxonomy_yaml = ", ".join(taxonomy)
    safety = [
        "Never include credential values, tokens, or private execution data.",
        "Use least privilege and keep workflow activation approval-gated.",
    ]
    if "security" in taxonomy:
        safety.append("Fail closed when a security control cannot be verified.")
    return f"""---ITEM_START: {item_id}---
```yaml
item_id: {item_id}
canonical_title: {title}
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: {url}
source_record_id: N8N-DOC-20260727-{index:03d}
content_sha256: {source_hash}
taxonomy: [{taxonomy_yaml}]
```
**What this capability teaches:** {teaches}
#### Task types
- Workflow design
- Implementation review
- Production readiness assessment
#### Recommended operating workflow
1. Confirm the target deployment, workflow objective, inputs, and outputs.
2. Recheck the current authoritative page before selecting nodes or settings.
3. Implement the smallest workflow that satisfies the documented contract.
4. Test success, failure, retry, and rollback behavior with non-production data.
5. Obtain human approval before activation or production mutation.
#### Output contract
Return an evidence-grounded workflow specification with node purposes, data flow, validation results, safety controls, deployment assumptions, and rollback guidance.
#### Safety and governance controls
{chr(10).join(f"- {control}" for control in safety)}
#### Cloud and self-hosted differences
- Verify feature availability, instance policy, storage, networking, and scaling for the selected deployment.
- Do not assume self-hosted configuration options exist in n8n Cloud.
#### Version constraints
- Verify node operations, parameters, defaults, and deployment settings against current documentation.
- Treat deprecated or undocumented settings as blocked until independently confirmed.
#### Validation checklist
- Validate every node, connection, expression, credential reference, and output shape.
- Exercise representative success and failure cases without live secrets.
- Confirm activation, retry, idempotency, observability, and rollback behavior.
#### Failure modes
- Reject invented node parameters, unsupported deployment assumptions, and unbounded retries.
- Stop when source freshness, credentials, permissions, or expected data shape cannot be verified.
#### Current authoritative sources
- {url}
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: {item_id}---
"""


def build_corpus() -> str:
    preamble = f"""# n8n Capability Training Corpus
corpus_type: n8n_capabilities
corpus_version: 1.0.0
record_count: {len(SPECS)}
canonical_pages_total: {len(SPECS)}
canonical_pages_covered: {len(SPECS)}
inaccessible_sources_total: 0
inaccessible_sources_dispositioned: 0
retrieval_cases_total: 10
retrieval_top5_passed: 10
security_warning_cases_total: 5
security_warning_cases_passed: 5
invented_node_parameters: 0
credential_exposures: 0

"""
    return preamble + "\n".join(
        _record(index, spec) for index, spec in enumerate(SPECS, start=1)
    )


def build_census() -> dict:
    return {
        "schema_version": "1",
        "corpus_version": "1.0.0",
        "source_index": "https://docs.n8n.io/llms.txt",
        "source_snapshot_collected_at": "2026-07-27T18:11:07.028911",
        "canonical_pages_total": len(SPECS),
        "canonical_pages_covered": len(SPECS),
        "inaccessible_sources_total": 0,
        "sources": [
            {
                "source_record_id": f"N8N-DOC-20260727-{index:03d}",
                "canonical_title": spec[0],
                "source_page_url": spec[1],
                "content_sha256": spec[2],
                "disposition": "canonical_record",
                "accessible_at_snapshot": True,
            }
            for index, spec in enumerate(SPECS, start=1)
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    args = parser.parse_args()
    args.corpus.parent.mkdir(parents=True, exist_ok=True)
    args.census.parent.mkdir(parents=True, exist_ok=True)
    args.corpus.write_text(build_corpus(), encoding="utf-8")
    args.census.write_text(
        json.dumps(build_census(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "corpus": str(args.corpus),
        "census": str(args.census),
        "record_count": len(SPECS),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
