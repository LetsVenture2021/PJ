# n8n Capability Training Corpus
corpus_type: n8n_capabilities
corpus_version: 1.0.0
record_count: 40
canonical_pages_total: 40
canonical_pages_covered: 40
inaccessible_sources_total: 0
inaccessible_sources_dispositioned: 0
retrieval_cases_total: 10
retrieval_top5_passed: 10
security_warning_cases_total: 5
security_warning_cases_passed: 5
invented_node_parameters: 0
credential_exposures: 0

---ITEM_START: N8N-001---
```yaml
item_id: N8N-001
canonical_title: Build a first workflow
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build-your-first-workflow.md
source_record_id: N8N-DOC-20260727-001
content_sha256: 719874a80e2f474686505bbc01dfaea4e9b05ced3e1a2b559ac436afbd16b74d
taxonomy: [workflow, trigger, credentials]
```
**What this capability teaches:** Compose a basic triggered workflow, map data, and test it before activation.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build-your-first-workflow.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-001---

---ITEM_START: N8N-002---
```yaml
item_id: N8N-002
canonical_title: Create and run workflows
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/understand-workflows/create-and-run-workflows.md
source_record_id: N8N-DOC-20260727-002
content_sha256: e4ae63e449cffa62486ef82f1b4a8b469d1b616a33a2cceab45bb3f3d03abebc
taxonomy: [workflow, execution, activation]
```
**What this capability teaches:** Create, manually test, save, and deliberately activate workflows.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/understand-workflows/create-and-run-workflows.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-002---

---ITEM_START: N8N-003---
```yaml
item_id: N8N-003
canonical_title: Create and edit credentials
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/understand-workflows/create-and-edit-credentials.md
source_record_id: N8N-DOC-20260727-003
content_sha256: a1fd2328ee67e3046fcc939527a3cae1490e3c0a263ee22e1939b9e8d8e7cab9
taxonomy: [credentials, authentication, least-privilege]
```
**What this capability teaches:** Configure credential objects without embedding secret values in workflows.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/understand-workflows/create-and-edit-credentials.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-003---

---ITEM_START: N8N-004---
```yaml
item_id: N8N-004
canonical_title: Export and import workflows
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/manage-workflows/export-and-import.md
source_record_id: N8N-DOC-20260727-004
content_sha256: da4e42641785e5f38bcc378e3c561191e3ee3de7f28acba9a9d315162dafab4f
taxonomy: [workflow, export, import]
```
**What this capability teaches:** Move workflow definitions while treating exported files as sensitive artifacts.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/manage-workflows/export-and-import.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-004---

---ITEM_START: N8N-005---
```yaml
item_id: N8N-005
canonical_title: Split flow with conditionals
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/split-with-conditionals.md
source_record_id: N8N-DOC-20260727-005
content_sha256: b1652e3eb91421e42e64a3c215daf6b3bc9b3e1b6fb1eaac05b8e1698b3f7dce
taxonomy: [flow-control, conditional, branching]
```
**What this capability teaches:** Route items through explicit conditional branches and define fallback behavior.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/split-with-conditionals.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-005---

---ITEM_START: N8N-006---
```yaml
item_id: N8N-006
canonical_title: Merge data branches
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/merge-data.md
source_record_id: N8N-DOC-20260727-006
content_sha256: 4688251a1282305d7ef7b74b988e86a9ec7b374d7c63b4223c1bc0ef7c56a084
taxonomy: [flow-control, merge, data]
```
**What this capability teaches:** Recombine branches using a merge strategy that preserves expected item shape.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/merge-data.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-006---

---ITEM_START: N8N-007---
```yaml
item_id: N8N-007
canonical_title: Loop over items
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/loop.md
source_record_id: N8N-DOC-20260727-007
content_sha256: 8c5d390222854066dde07169c1eff548db44269b54cdc7d0f1e3a011b8a4b399
taxonomy: [flow-control, loop, batching]
```
**What this capability teaches:** Process repeated items with explicit completion and bounded iteration behavior.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/loop.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-007---

---ITEM_START: N8N-008---
```yaml
item_id: N8N-008
canonical_title: Wait and resume execution
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/wait.md
source_record_id: N8N-DOC-20260727-008
content_sha256: e8f8ce6c190fe0c1b3419ab208dea383155f1faf30d2d9e22f80c3181c36a31d
taxonomy: [flow-control, wait, resume]
```
**What this capability teaches:** Pause and resume workflows using time or event-based continuation.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/wait.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-008---

---ITEM_START: N8N-009---
```yaml
item_id: N8N-009
canonical_title: Break workflows into sub-workflows
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/break-workflows-into-smaller-parts.md
source_record_id: N8N-DOC-20260727-009
content_sha256: 375ec6cda438a0753caace73ca332c83684b9e9710b4471ae42f1d4d2f5be192
taxonomy: [workflow, sub-workflow, reuse]
```
**What this capability teaches:** Extract reusable sub-workflows with explicit inputs, outputs, and ownership.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/break-workflows-into-smaller-parts.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-009---

---ITEM_START: N8N-010---
```yaml
item_id: N8N-010
canonical_title: Handle errors gracefully
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/handle-errors-gracefully.md
source_record_id: N8N-DOC-20260727-010
content_sha256: 39b3bd9b996317e2eb41bce2396455e78f00cf9807582fb665220284f36965b9
taxonomy: [reliability, error-workflow, alerting]
```
**What this capability teaches:** Attach error workflows, preserve diagnostic context, and notify operators.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/handle-errors-gracefully.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-010---

---ITEM_START: N8N-011---
```yaml
item_id: N8N-011
canonical_title: Understand execution order
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/flow-logic/understand-execution-order.md
source_record_id: N8N-DOC-20260727-011
content_sha256: fc60d08ea37dbc0f891431ef123a47d44a12df59bf9f54979ce24532efde15ec
taxonomy: [flow-control, execution-order, determinism]
```
**What this capability teaches:** Reason about branch execution order before relying on side effects.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/flow-logic/understand-execution-order.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-011---

---ITEM_START: N8N-012---
```yaml
item_id: N8N-012
canonical_title: Understand the n8n data structure
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/understand-n8ns-data-structure.md
source_record_id: N8N-DOC-20260727-012
content_sha256: d60f62866fbcbcc65d63930b0e872d67fa8ebf990523379fd12188abb9cef15c
taxonomy: [data, items, json]
```
**What this capability teaches:** Model node input and output as item arrays containing JSON and binary data.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/understand-n8ns-data-structure.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-012---

---ITEM_START: N8N-013---
```yaml
item_id: N8N-013
canonical_title: Map data with the UI
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/reference-data/use-the-ui-mapper.md
source_record_id: N8N-DOC-20260727-013
content_sha256: f8b9eed75b95053c53fddcd3a3117026500f9101d4161eb5bfdb197def3dea67
taxonomy: [data, mapping, expressions]
```
**What this capability teaches:** Map upstream fields through the UI while validating sample item shape.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/reference-data/use-the-ui-mapper.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-013---

---ITEM_START: N8N-014---
```yaml
item_id: N8N-014
canonical_title: Link data items
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/reference-data/link-data-items.md
source_record_id: N8N-DOC-20260727-014
content_sha256: 4ee93d1634176609051a3489d9a8d3fe88c7e2a457b10480c665a744a736c95e
taxonomy: [data, item-linking, lineage]
```
**What this capability teaches:** Preserve item lineage so downstream expressions resolve the correct source item.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/reference-data/link-data-items.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-014---

---ITEM_START: N8N-015---
```yaml
item_id: N8N-015
canonical_title: Choose a data transformation approach
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/transform-data/approaches-for-transforming-data.md
source_record_id: N8N-DOC-20260727-015
content_sha256: 563a5c3aba75f1753fbe5e79b72b77fd2bf56414e0da99a1024e394fe1e9b651
taxonomy: [data, transformation, nodes]
```
**What this capability teaches:** Select expressions, transformation nodes, or code based on complexity and risk.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/transform-data/approaches-for-transforming-data.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-015---

---ITEM_START: N8N-016---
```yaml
item_id: N8N-016
canonical_title: Transform data with expressions
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/transform-data/expressions-for-data-transformation.md
source_record_id: N8N-DOC-20260727-016
content_sha256: 5df74f50a6989dd92949f546baf8aa08cd1af4ebbb7584d7654ef8a2e302c2b3
taxonomy: [data, expressions, transformation]
```
**What this capability teaches:** Apply bounded expressions and validate null, type, and missing-field behavior.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/transform-data/expressions-for-data-transformation.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-016---

---ITEM_START: N8N-017---
```yaml
item_id: N8N-017
canonical_title: Filter unwanted data
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/filter-out-unwanted-data.md
source_record_id: N8N-DOC-20260727-017
content_sha256: 1cca1550e8c18c4659d3d2e7afa2c320d664c074ab95deaf130c4ff270586470
taxonomy: [data, filter, validation]
```
**What this capability teaches:** Remove items using explicit filter criteria and verify edge cases.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/filter-out-unwanted-data.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-017---

---ITEM_START: N8N-018---
```yaml
item_id: N8N-018
canonical_title: Pin and mock data
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/pin-and-mock-data.md
source_record_id: N8N-DOC-20260727-018
content_sha256: 1a8357f02b8a1badd8a04009542d938298948c2647fcfbfd183fb74b38a64d77
taxonomy: [testing, mock-data, pinning]
```
**What this capability teaches:** Use pinned or mocked samples for development without treating them as live data.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/pin-and-mock-data.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-018---

---ITEM_START: N8N-019---
```yaml
item_id: N8N-019
canonical_title: Work with files and images
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-files-and-images.md
source_record_id: N8N-DOC-20260727-019
content_sha256: e72b3096c3415835dd8ee6b8868f6bb4248c0db30ca5244e77cf59cf4487ee49
taxonomy: [data, binary, files]
```
**What this capability teaches:** Handle binary file properties without losing metadata or exhausting storage.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/handle-special-data-types/work-with-files-and-images.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-019---

---ITEM_START: N8N-020---
```yaml
item_id: N8N-020
canonical_title: Use data tables
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/work-with-data/data-tables.md
source_record_id: N8N-DOC-20260727-020
content_sha256: d20dd7ba9418265dea2282ccdee5580a6064892d086ef189d3b2922d14daf1ed
taxonomy: [data, tables, persistence]
```
**What this capability teaches:** Use data tables for governed workflow state with explicit schema and retention.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/work-with-data/data-tables.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-020---

---ITEM_START: N8N-021---
```yaml
item_id: N8N-021
canonical_title: Use the Code node
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/code-in-n8n/using-the-code-node.md
source_record_id: N8N-DOC-20260727-021
content_sha256: f1476f84f1b7746531864e92fe21c42f3a38e67f0f948f8616148d911e17e5c8
taxonomy: [code, javascript, python]
```
**What this capability teaches:** Use Code nodes only when standard nodes cannot express the transformation safely.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/code-in-n8n/using-the-code-node.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-021---

---ITEM_START: N8N-022---
```yaml
item_id: N8N-022
canonical_title: Define custom variables
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/build/code-in-n8n/define-custom-variables.md
source_record_id: N8N-DOC-20260727-022
content_sha256: 837abb0a8ae9a71b2b0bc716c6b3c6815c3d4e39968b1cea83e3a1c5fcb7c708
taxonomy: [configuration, variables, environments]
```
**What this capability teaches:** Reference non-secret environment-specific values through governed variables.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/build/code-in-n8n/define-custom-variables.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-022---

---ITEM_START: N8N-023---
```yaml
item_id: N8N-023
canonical_title: Call APIs with HTTP Request
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest.md
source_record_id: N8N-DOC-20260727-023
content_sha256: 4a9a97c8cb88de1550e9d60ae00b8717729da82fcd42f0eabd1167d6385e7171
taxonomy: [integration, http, api]
```
**What this capability teaches:** Configure HTTP requests from current node documentation and validate responses.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-023---

---ITEM_START: N8N-024---
```yaml
item_id: N8N-024
canonical_title: Schedule workflow execution
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
source_record_id: N8N-DOC-20260727-024
content_sha256: 33d3f26b8282cc058b0775e1a6f44d988c628216a3976559565f4329cb14160e
taxonomy: [trigger, schedule, timezone]
```
**What this capability teaches:** Schedule executions with explicit timezone, interval, and activation review.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.scheduletrigger.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-024---

---ITEM_START: N8N-025---
```yaml
item_id: N8N-025
canonical_title: Receive events with Webhook
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook.md
source_record_id: N8N-DOC-20260727-025
content_sha256: 73a34dbeb8bd30206ef94cfbc7d6ee2d054e3813530aad6781e062d533ea4de6
taxonomy: [trigger, webhook, http]
```
**What this capability teaches:** Expose test and production webhook endpoints with authentication and validation.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.webhook.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-025---

---ITEM_START: N8N-026---
```yaml
item_id: N8N-026
canonical_title: Respond to webhook calls
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook.md
source_record_id: N8N-DOC-20260727-026
content_sha256: e70bcaed44d232e423509f87f7100b9b71e6a7347e6a31a2568f9633b858cd13
taxonomy: [webhook, response, http]
```
**What this capability teaches:** Return bounded status, headers, and body data without leaking internal details.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.respondtowebhook.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-026---

---ITEM_START: N8N-027---
```yaml
item_id: N8N-027
canonical_title: Configure credential environment variables
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/credentials.md
source_record_id: N8N-DOC-20260727-027
content_sha256: dc2b2881c1d9d7b120d8e694c2e9464bae9ac0e8f54d35086f7e5a7e9fe3b64a
taxonomy: [security, credentials, configuration]
```
**What this capability teaches:** Configure credential behavior through deployment controls without storing secrets in source.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
- Fail closed when a security control cannot be verified.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/credentials.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-027---

---ITEM_START: N8N-028---
```yaml
item_id: N8N-028
canonical_title: Use external secrets
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/external-secrets.md
source_record_id: N8N-DOC-20260727-028
content_sha256: 8cf16f6cd3ae64bf5dce4ce8c0e19dce04d5482b012f17ae0549a8ad93bf3e59
taxonomy: [security, external-secrets, vault]
```
**What this capability teaches:** Retrieve secrets from an external manager using least-privilege access.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
- Fail closed when a security control cannot be verified.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/basic-configuration/use-environment-variables/external-secrets.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-028---

---ITEM_START: N8N-029---
```yaml
item_id: N8N-029
canonical_title: Manage security policies
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/manage-security-policies.md
source_record_id: N8N-DOC-20260727-029
content_sha256: 149c1b5711bb33ad289543c5f6b6694577cd42988dd3e18db4c1fe260796cc73
taxonomy: [security, policy, hardening]
```
**What this capability teaches:** Apply instance security policy settings and verify enforcement before release.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
- Fail closed when a security control cannot be verified.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/manage-security-policies.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-029---

---ITEM_START: N8N-030---
```yaml
item_id: N8N-030
canonical_title: Enable SSRF protection
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md
source_record_id: N8N-DOC-20260727-030
content_sha256: a198f9eb539c2cf9a34bc03c28b7743807d0b9052993bbd0ac3dcd6338213d10
taxonomy: [security, ssrf, network]
```
**What this capability teaches:** Block server-side request forgery paths and constrain outbound destinations.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
- Fail closed when a security control cannot be verified.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/enable-ssrf-protection.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-030---

---ITEM_START: N8N-031---
```yaml
item_id: N8N-031
canonical_title: Redact execution data
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/redact-execution-data.md
source_record_id: N8N-DOC-20260727-031
content_sha256: 067130e238ebccfdd7e23e5a68deed715f980daa83bde70e7f5c7e3bef4667e6
taxonomy: [security, redaction, execution-data]
```
**What this capability teaches:** Redact sensitive execution fields before they reach logs or stored diagnostics.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
- Fail closed when a security control cannot be verified.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/security/redact-execution-data.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-031---

---ITEM_START: N8N-032---
```yaml
item_id: N8N-032
canonical_title: Scale self-hosted n8n
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling.md
source_record_id: N8N-DOC-20260727-032
content_sha256: e4c6a6156dad9b94fef65af232322dc7789001d5c3fcab16ea3f2aa12ed02984
taxonomy: [deployment, scaling, capacity]
```
**What this capability teaches:** Plan scaling around workload, database, storage, worker, and webhook constraints.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-032---

---ITEM_START: N8N-033---
```yaml
item_id: N8N-033
canonical_title: Enable queue mode
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode.md
source_record_id: N8N-DOC-20260727-033
content_sha256: 61efdf67b0e0c1f40a6e0e8d9f5119c7d42c21996c719206cae44a00b7990d57
taxonomy: [deployment, queue-mode, workers]
```
**What this capability teaches:** Separate main and worker processes with supported database, broker, and storage.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/enable-queue-mode.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-033---

---ITEM_START: N8N-034---
```yaml
item_id: N8N-034
canonical_title: Control concurrency
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency.md
source_record_id: N8N-DOC-20260727-034
content_sha256: a09637c8501c1e8c06e2d6775ce33382ddde52a50493632485e056fbe57f5fa1
taxonomy: [deployment, concurrency, capacity]
```
**What this capability teaches:** Set concurrency from measured capacity and verify queue backpressure.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/deploy/host-n8n/configure-n8n/scaling/control-concurrency.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-034---

---ITEM_START: N8N-035---
```yaml
item_id: N8N-035
canonical_title: Set up logging
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/set-up-logging.md
source_record_id: N8N-DOC-20260727-035
content_sha256: a3c5db9cee9ae3b512bdb53dfa6a56ae266d57bf1769ceacda9d8e2a91f0d6de
taxonomy: [operations, logging, diagnostics]
```
**What this capability teaches:** Configure useful logs while excluding credentials and sensitive execution data.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/set-up-logging.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-035---

---ITEM_START: N8N-036---
```yaml
item_id: N8N-036
canonical_title: Monitor n8n
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/monitor-n8n.md
source_record_id: N8N-DOC-20260727-036
content_sha256: 442f315c6043cba52b19566f2f51ba84e066eb76d719f0b812585358ac6a3ccd
taxonomy: [operations, monitoring, health]
```
**What this capability teaches:** Monitor health and execution behavior with alerts tied to operator runbooks.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/deploy/host-n8n/keep-n8n-running/monitor-n8n.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-036---

---ITEM_START: N8N-037---
```yaml
item_id: N8N-037
canonical_title: Assess community node risks
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/integrations/community-nodes/risks.md
source_record_id: N8N-DOC-20260727-037
content_sha256: 6774b9ace070f1c0171f2077c88d0e75711da138d64fdc2536f2953afae4831e
taxonomy: [security, community-nodes, supply-chain]
```
**What this capability teaches:** Review unverified node code, permissions, maintenance, and supply-chain risk.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
- Fail closed when a security control cannot be verified.
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
- https://docs.n8n.io/integrations/community-nodes/risks.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-037---

---ITEM_START: N8N-038---
```yaml
item_id: N8N-038
canonical_title: Authenticate to the n8n API
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/connect/n8n-api/authentication.md
source_record_id: N8N-DOC-20260727-038
content_sha256: e1e3b57d1cd5bdfb8a3c1742c0ce4c7805fcc759b47dd455d64925da6197cbcd
taxonomy: [api, authentication, credentials]
```
**What this capability teaches:** Authenticate API clients without exposing keys in workflow content or logs.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/connect/n8n-api/authentication.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-038---

---ITEM_START: N8N-039---
```yaml
item_id: N8N-039
canonical_title: Organize work in projects with RBAC
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/organize-work-in-projects.md
source_record_id: N8N-DOC-20260727-039
content_sha256: 911b8ab72684c10f27e22320c8548011a63441f47a02d3d6f7c8acb8320e726d
taxonomy: [governance, projects, rbac]
```
**What this capability teaches:** Use projects, roles, and least privilege to separate workflow ownership.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/administer/manage-users-and-access/set-permissions-and-roles-rbac/organize-work-in-projects.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-039---

---ITEM_START: N8N-040---
```yaml
item_id: N8N-040
canonical_title: Use source control and environments
domain: n8n
surface: Cloud and self-hosted
version_scope: verify_current
corpus_status: active
requires_current_docs_check: true
source_page_url: https://docs.n8n.io/administer/use-source-control-and-environments.md
source_record_id: N8N-DOC-20260727-040
content_sha256: e3ef4e0979c2379c8ac34400d3117999e0523d26b42501dd2b5e006fe61dc592
taxonomy: [deployment, source-control, environments]
```
**What this capability teaches:** Promote reviewed workflow changes across environments with source control.
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
- Never include credential values, tokens, or private execution data.
- Use least privilege and keep workflow activation approval-gated.
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
- https://docs.n8n.io/administer/use-source-control-and-environments.md
#### Freshness requirements
- Revalidate the source before deployment and after n8n upgrades or node-version changes.
- Record the source URL and content hash used for the implementation decision.
#### Approval policy
Human approval is required before activation, credential changes, external side effects, or production deployment.
---ITEM_END: N8N-040---
