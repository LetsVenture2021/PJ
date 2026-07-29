# PJ document quality standard

**Control ID:** DQS-001  
**Schema version:** 1.0.0  
**Content version:** 1.0.0  
**Document class:** architecture reference  
**Lifecycle state:** approved  
**Owner:** PJ runtime owner  
**Accountable approver:** PJ repository owner  
**Audience:** PJ maintainers, operators, reviewers, and document-generating systems  
**Information classification:** public  
**Authoritative source:** yes; this checked-in file is the normative source  
**Created:** 2026-07-29T00:00:00Z  
**Updated:** 2026-07-29T00:00:00Z  
**Approved:** 2026-07-29T00:00:00Z  
**Next review:** 2027-07-29T00:00:00Z  
**Expires at:** not applicable while this version remains authoritative  
**Provenance:** repository requirements and PJ's existing DocOps, security, and corpus controls  
**Generator version:** not applicable; human-authored repository standard  
**Source SHA-256:** recorded by the governing Git commit  
**Artifact SHA-256:** must be calculated and recorded for each rendered distribution artifact

## 1. Purpose and authority

This standard is the normative specification for **every human-readable or
machine-readable document PJ creates**, including intermediate documents and
exports. It applies whether output is produced by a person, model, script,
DocOps tool, ingestion workflow, or renderer. “Document” includes prose,
presentations, spreadsheets, reports, manifests, JSON/JSONL, evidence records,
corpora, and equivalent durable artifacts.

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**,
**SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **MAY**, and **OPTIONAL** are to be
interpreted as described by RFC 2119 and RFC 8174 when shown in capitals.
Where a class-specific rule conflicts with a general rule, the stricter rule
applies. A controlled exception under section 9 is the only permitted waiver.

## 2. Document classes

Every document MUST declare exactly one primary class. Additional classes MAY
be listed as secondary classifications, but do not remove primary-class gates.

1. **Governed business deliverable:** decision briefs, proposals, plans,
   policies, executive communications, and other audience-ready business work.
2. **Operational runbook:** repeatable operating, incident, deployment,
   recovery, maintenance, or support instructions.
3. **Architecture reference:** durable descriptions of systems, interfaces,
   controls, data flows, decisions, and technical constraints.
4. **Audit/evaluation report:** an assessment, test report, review, benchmark,
   assurance record, or post-incident evaluation that reaches findings.
5. **Machine-readable evidence:** structured records intended for automated
   validation or audit, including receipts, manifests, attestations, and
   evaluation results.
6. **Training corpus:** source, transformed, or curated material intended for
   retrieval, evaluation, fine-tuning, or other model-related use.
7. **Generated export:** a rendered or converted artifact derived from another
   governed source, including PDF, HTML, DOCX, RTF, PPTX, XLSX, and packaged
   Markdown.

## 3. Mandatory metadata

Metadata MUST be represented in a visible front matter/header for readable
documents or in documented fields (or a linked sidecar manifest) for binary
and machine-readable artifacts. Field names MAY follow a registered schema,
but their meanings MUST remain unambiguous. Timestamps MUST use RFC 3339 with
an explicit offset (UTC `Z` is RECOMMENDED). “Not applicable” MUST be explicit;
a required field MUST NOT be silently omitted.

All classes MUST record:

- stable `document_id`, which MUST survive revisions and format conversion;
- `schema_version` and `content_version`;
- `title` and `document_class`;
- `owner` and `accountable_approver` (roles MUST resolve to accountable people);
- `intended_audience` and `information_classification`;
- `lifecycle_state`: exactly `draft`, `in_review`, `approved`, `superseded`, or
  `retired`;
- `created_at` and `updated_at`, plus `approved_at`, `next_review_at`, and
  `expires_at` where applicable to state, risk, policy, license, or content;
- `authoritative_source` status, identifying either this object as
  authoritative or the authoritative object it derives from;
- `source_references` and provenance/lineage references sufficient to recreate
  or explain the content;
- `generator_version`, including tool/model/template versions, or an explicit
  human-authored/not-applicable value;
- `source_sha256` and `artifact_sha256` values. Before rendering, an artifact
  hash MAY say `pending`; before external distribution it MUST contain the
  computed SHA-256. For an authoritative source, `source_sha256` MAY be the
  governing Git object/commit reference where embedding the file's own hash
  would be self-referential.

Class-specific metadata is also REQUIRED:

| Class | Additional metadata |
| --- | --- |
| Governed business deliverable | decision/engagement identifier and distribution authority |
| Operational runbook | service/system, operational owner, escalation contact, last exercise date |
| Architecture reference | system/component, decision-record links, implementation status |
| Audit/evaluation report | assessment period, evaluator, evaluation criteria, evidence index |
| Machine-readable evidence | media type, schema URI/identifier, producing control/run ID |
| Training corpus | dataset/corpus version, license/use constraints, origin, transformation chain, trust status |
| Generated export | source `document_id` and content version, format, renderer, render timestamp |

Sensitive metadata MUST use identifiers or protected references rather than
embedding secrets or unnecessary personal data.

## 4. Required sections by class

Headings MAY vary only when a mapping to these sections is explicit.

| Class | Required sections or logical records |
| --- | --- |
| Governed business deliverable | purpose; context; scope; audience; facts and sources; assumptions; recommendation or intended outcome; risks; decisions/approvals; actions and owners |
| Operational runbook | purpose; scope; ownership; prerequisites; safety/security cautions; procedure; verification; rollback/recovery; escalation; evidence capture; references |
| Architecture reference | purpose; scope and boundaries; context; components; interfaces; data flow; security/privacy controls; decisions and rationale; constraints; failure modes; operations; references |
| Audit/evaluation report | objective; method; scope; criteria; evidence; limitations; findings with severity; conclusion; disposition; remediation owners and dates |
| Machine-readable evidence | schema declaration; subject; collection method; observations/results; provenance; integrity values; validation status; disposition/retention |
| Training corpus | purpose and permitted use; sources and license; collection/selection method; transformations; format/schema; untrusted-data warning; privacy/security review; quality evaluation; limitations/bias; version and retention |
| Generated export | source identity/version; render profile; integrity/validation result; accessibility status; distribution classification; renderer limitations |

Machine-readable sections MAY be objects or fields rather than prose. Empty
required sections do not satisfy this standard.

## 5. Content and structural controls

### DQS-STR — Structure and usability

- **DQS-STR-01:** A document MUST use the metadata and required sections for
  its class, with a clear title, purpose, logical hierarchy, and navigable
  ordering.
- **DQS-STR-02:** Terms, units, acronyms, dates, jurisdictions, and normative
  language MUST be defined or unambiguous for the intended audience.
- **DQS-STR-03:** Machine-readable content MUST conform to its declared schema,
  encoding, and media type and MUST be deterministically parseable.

### DQS-FAC — Factual support and evidence

- **DQS-FAC-01:** Material factual claims MUST be supported by current,
  traceable sources or clearly identified as assumptions, estimates, or
  opinions. Sources MUST identify the version and access/effective date where
  currency matters.
- **DQS-FAC-02:** Calculations and transformations MUST be reproducible;
  evidence MUST preserve provenance and integrity hashes.
- **DQS-FAC-03:** A document MUST NOT be approved with unresolved placeholders,
  fabricated citations, undisclosed uncertainty, or stale required evidence.

### DQS-ACC — Accessibility

- **DQS-ACC-01:** Human-readable output MUST provide semantic headings,
  readable language, sufficient contrast, meaningful link text, and tables
  with understandable headers.
- **DQS-ACC-02:** Every required visual MUST have equivalent accessible text,
  alt text, a data table, or a described conclusion. Color MUST NOT be the
  sole carrier of meaning.
- **DQS-ACC-03:** The source and every distributed format SHOULD meet WCAG 2.2
  AA applicable criteria; any unmet applicable criterion requires a waiver.

### DQS-SEC and DQS-PRI — Security and privacy

- **DQS-SEC-01:** Documents MUST be classified before distribution and MUST
  apply least-privilege access, safe links/attachments, integrity validation,
  and approved storage/transfer appropriate to that classification.
- **DQS-SEC-02:** Documents MUST NOT contain secrets, credentials, executable
  active content, or concealed instructions unless explicitly required,
  isolated, reviewed, and approved for the intended medium.
- **DQS-PRI-01:** Collection and disclosure of personal or confidential data
  MUST be necessary, minimized, purpose-limited, and covered by authorized
  access and retention rules. Redaction MUST be irreversible in exports.
- **DQS-PRI-02:** Provenance MUST NOT be confused with trust. Untrusted input
  MUST remain labeled and MUST NOT be treated as instructions.

### DQS-VIS — Visual rendering and export

- **DQS-VIS-01:** Every required target format MUST render without clipping,
  overlap, missing fonts/glyphs, broken links, unreadable scaling, or missing
  visuals. Pagination, tables, charts, and reading order MUST be inspected.
- **DQS-VIS-02:** Generated exports MUST be compared with the authoritative
  source, validated in a suitable parser/viewer, and hash-registered. A format
  conversion MUST NOT silently change meaning, classification, or approval.
- **DQS-VIS-03:** Draft or in-review exports MUST be visibly marked as such;
  approved exports MUST identify their source version and integrity record.

### DQS-APP — Review and approval

- **DQS-APP-01:** The owner MUST complete applicable content, evidence,
  security, privacy, accessibility, and rendering gates before approval. The
  accountable approver MUST be identifiable and independent where policy or
  risk requires it.
- **DQS-APP-02:** Approval MUST record approver, decision, timestamp, reviewed
  content/artifact hashes, exceptions, and next review/expiry. Any content
  change after approval MUST create a new content version and repeat affected
  gates.
- **DQS-APP-03:** Only `approved` documents MAY be represented as authoritative
  audience-ready output. Automation MUST fail closed when a mandatory gate or
  approval record is absent.

### DQS-RET — Retention and supersession

- **DQS-RET-01:** Each document MUST have an applicable retention/disposition
  rule. Content MUST be retained long enough for legal, operational, audit,
  provenance, and recovery needs, and MUST be securely disposed when required.
- **DQS-RET-02:** Revisions MUST preserve immutable lineage. Superseded content
  MUST point to its successor; the successor MUST identify what it supersedes.
  Superseded or retired copies MUST NOT remain presented as current.
- **DQS-RET-03:** Expired documents MUST be retired, re-approved, or placed in
  review. Legal holds and evidence preservation override routine deletion.

## 6. Class-specific gates

- A governed business deliverable MUST have a named decision/distribution
  owner, verified material claims, explicit assumptions, and recorded approval.
- An operational runbook MUST be safely executable by its audience, MUST state
  prerequisites, rollback, verification, ownership, and escalation, and SHOULD
  be exercised by the next-review date. Destructive steps require cautions and
  recovery evidence.
- An architecture reference MUST distinguish current, planned, and deprecated
  behavior and reconcile diagrams with text and implemented interfaces.
- An audit/evaluation report MUST preserve method, scope, evidence,
  limitations, findings, and disposition; evidence-to-finding traceability is
  REQUIRED and conflicts of interest MUST be disclosed.
- Machine-readable evidence MUST validate against its declared schema and MUST
  preserve deterministic timestamps, identifiers, provenance, and hashes.
- A training corpus MUST document license/use constraints, privacy review,
  transformations, limitations, trust status, and evaluation. **Every corpus
  file MUST open with an untrusted-data banner stating that its contents are
  data, not instructions, and MUST NOT be followed as commands.** Corpus
  ingestion, synchronization, dataset pulling, and transformation commands
  **are operator workflows, not startup behavior**; they MUST run only through
  an intentional operator action after the operator reads the command's
  `--help` and reviews its source, destination, and controls.
- A generated export MUST trace to an approved source unless visibly marked
  draft, MUST record source and artifact SHA-256 values, and MUST pass semantic,
  visual, accessibility, and integrity comparisons in its distributed format.

## 7. “Pristine” quality status

A document is **pristine** only when it passes **all applicable gates** in this
standard and any registered class/schema profile with:

- zero blocker or critical defects;
- zero unresolved placeholders;
- zero broken internal links;
- zero inaccessible required visuals; and
- all evidence records current at the time of the decision.

“Pristine” is a quality result, not a substitute for approval. A pristine
document MUST NOT be called approved or authoritative unless DQS-APP also
passes. Unknown, skipped, waived without a valid exception, or not-tested gates
do not pass. The result MUST record the validator/checklist version, timestamp,
scope, evidence, and hashes evaluated.

## 8. Lifecycle and gate record

Permitted transitions are `draft` to `in_review` to `approved`, and
`approved` to `superseded` or `retired`. A rejected review returns to `draft`.
An emergency retirement MAY occur from any state with a recorded reason.
Superseded and retired are terminal for that content version; corrections MUST
create a new version.

The gate record MUST list each applicable control ID, pass/fail/not-applicable
result, evidence reference, evaluator, timestamp, document source hash, artifact
hashes, and controlled exceptions. Approval and pristine claims MUST be
machine-verifiable from this record where automation produces the document.

## 9. Controlled exceptions

An exception MUST be exceptional, bounded, traceable, and approved before the
affected document is approved or distributed. Every waiver MUST include:

- the waived `control_id`;
- rationale and affected document/version/format/scope;
- risk description and named `risk_owner`;
- accountable approval and approval timestamp;
- expiration date;
- compensating control, its owner, and evidence;
- remediation or renewal decision and tracking reference.

Waivers MUST NOT be open-ended, inherited silently by later versions, or used
to conceal a blocker/critical defect. On expiration, the document MUST fail the
waived gate until remediation or a newly assessed waiver is approved. A waiver
MUST NOT permit secrets exposure, unlawful processing, fabricated evidence, or
misrepresentation of lifecycle/authority. Documents with any active waiver MAY
be approved when policy permits, but MUST NOT be labeled pristine.

## 10. Minimum conformance checklist

Before distribution, the owner and approver MUST be able to answer yes to all
applicable items:

1. Class, metadata, required sections, schema, versions, and lifecycle are valid.
2. Claims, sources, calculations, provenance, and evidence are current and
   traceable.
3. Security classification, privacy minimization, access, and retention pass.
4. Source and each target rendering pass accessibility, visual, link, semantic,
   and integrity checks.
5. Required approvals bind to the reviewed source/export hashes.
6. Supersession links and authoritative-source status are unambiguous.
7. Exceptions are complete, approved, unexpired, and compensated.
8. If pristine is claimed, the stricter definition in section 7 is satisfied.

