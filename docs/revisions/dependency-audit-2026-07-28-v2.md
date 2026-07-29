---
document_id: pj.docs.dependency-audit-2026-07-28
version: 2
status: approved
template_id: dependency_audit
template_version: 1
supersedes: docs/dependency-audit-2026-07-28.md
prior_sha256: 7ea0ea707de85149e89e4e8a369995a079fb2e7db5014686ba4f80d26e7df4f2
change_note: Added provenance, review, and approval metadata; authored content is unchanged.
provenance: Repository-authored operational documentation.
reviewed_by: repository-owner
reviewed_at: 2026-07-29
approved_by: repository-owner
approved_at: 2026-07-29
---

# Dependency audit report — July 28, 2026

## Scope

- Python runtime dependencies in `requirements.txt`
- Python development dependencies in `requirements-dev.txt`
- Worker and client JavaScript dependencies in `package.json` and `package-lock.json`

Every Python requirement and direct npm dependency is pinned to an explicit
version. The npm lockfile pins the complete transitive JavaScript dependency
graph. The Worker and browser client do not import third-party npm packages at
runtime; the declared npm packages are development-only quality tools.

## Results

| Ecosystem | Tool | Initial dependencies | Initial high / critical | Final dependencies | Final high / critical |
| --- | --- | ---: | ---: | ---: | ---: |
| Python runtime | `pip-audit 2.10.1` | 54 | 0 / 0 | 54 | 0 / 0 |
| Python development | `pip-audit 2.10.1` | 41 | 0 / 0 | 41 | 0 / 0 |
| JavaScript | `npm audit` with npm 11.6.2 | 88 | 5 / 0 | 72 | 0 / 0 |

## JavaScript remediation detail

All five JavaScript findings traced to the ESLint 9 development chain and
[GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg).
Remediation steps:

- Upgraded `eslint` from `9.39.5` to `10.8.0`
- Upgraded `@eslint/js` from `9.39.5` to `10.0.1`
- Regenerated `package-lock.json`, which resolved:
  - `minimatch` to `10.2.6`
  - `brace-expansion` to `5.0.8`

Final npm and Python audits report no known vulnerabilities.

## Risk acceptance

No high or critical vulnerability remains, so no risk acceptance is required.

## Ongoing controls

- CI enforces:
  - `python -m pip_audit --requirement requirements.txt`
  - `python -m pip_audit --requirement requirements-dev.txt`
  - `npm audit --audit-level=high`
- Dependabot continues weekly monitoring for root Python and npm manifests.
