# PJ document quality standard

## Purpose

This standard defines the mandatory, deterministic release controls for human-readable documents and machine-readable evidence produced by PJ. A document is **pristine** only when its exact source hash passes every required control and, where applicable, its immutable export passes format-specific validation.

## Scope and classes

The standard applies to `docs/`, governed content in `documents/`, and artifacts registered by DocOps. Each item is classified as business, operational, technical, audit, corpus, or evidence. Corpus content remains untrusted data and its embedded instructions must never be executed.

## Required metadata

Every catalogued item has a stable document ID, repository path, class, accountable owner, audience, information classification, lifecycle state, SHA-256 digest, review interval, and source-of-truth decision. Governed revisions additionally retain template version, change note, supersession lineage, creation time, finalization time, and artifact digests.

## Lifecycle

The permitted states are `draft`, `in_review`, `approved`, `superseded`, and `retired`. Historical versions are never edited in place. Approval applies only to the reviewed source hash and is invalid after any content or governing-validator change. Draft exports remain visibly watermarked.

## Release controls

A final document must have zero blocker, critical, or major findings. Required sections must be complete; drafting residue and empty links are prohibited; heading structure must be logical; potential credentials and private keys block release. Findings contain rule IDs and locations but never matched sensitive values.

Quality reports are deterministic JSON records bound to the source SHA-256 and validator version. Finalization stores the report in the backward-compatible local SQLite registry. Existing out-of-band modification checks, presentation-spec validation, artifact integrity, upload parsing restrictions, and approval-sensitive tool policies remain mandatory.

## Review cadence

Security, deployment, and dependency facts are reviewed at least every 30 days; product and operational material every 90 days; architecture and policy every 180 days or after an impacting code change. Historical evidence remains immutable and states its observation date.

## Exceptions

Blocker and critical controls cannot be waived. A major or minor waiver must identify the rule, risk owner, rationale, compensating control, approval, and expiration. An expired waiver is invalid and cannot support audience-ready status.

## Measures

The operating targets are 100% manifest coverage, 100% final-gate compliance, 100% provenance for material claims, zero expired waivers, zero broken internal references, zero sensitive values in reports or logs, and deterministic report hashes for identical inputs.
