# Dependency vulnerability audit - July 28, 2026

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

The audits used the vulnerability data available on July 28, 2026. A clean
result is point-in-time evidence, not a guarantee that future advisories will
not affect these versions.

## Remediation

The JavaScript audit traced all five high-severity findings to the ESLint 9
development dependency chain:

`eslint` -> `@eslint/config-array` / `@eslint/eslintrc` -> `minimatch` ->
`brace-expansion`

The underlying advisory was
[GHSA-mh99-v99m-4gvg](https://github.com/advisories/GHSA-mh99-v99m-4gvg), an
unbounded brace expansion denial of service. The affected packages were not
shipped with the Worker, but the findings were remediated rather than accepted:

| Direct package | Previous version | Remediated version |
| --- | ---: | ---: |
| `eslint` | 9.39.5 | 10.8.0 |
| `@eslint/js` | 9.39.5 | 10.0.1 |

ESLint 10 requires Node.js 20.19.0 or newer, so the package engine declaration,
CI runtime, and local prerequisite now enforce that floor. The regenerated
lockfile resolves `minimatch` to 10.2.6 and `brace-expansion` to 5.0.8, both
outside the affected ranges. A follow-up `npm audit` found zero vulnerabilities.

## Risk decisions and ongoing controls

No high or critical vulnerability remains, so no risk acceptance is required.
CI now runs `pip-audit` against the Python runtime requirements and `npm audit
--audit-level=high` against the locked JavaScript graph. Dependabot continues
to monitor both ecosystems weekly.
