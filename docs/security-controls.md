# GitHub security controls

This repository uses GitHub-native dependency and secret scanning controls. A
repository administrator should review this checklist after repository
transfers, visibility changes, or organization policy changes.

## Enablement checklist

The following settings were verified on July 28, 2026:

- [x] In **Settings > Advanced Security**, enable **Dependency graph**.
- [x] In **Settings > Advanced Security**, enable **Dependabot alerts** and
  **Dependabot security updates**.
- [x] In **Settings > Advanced Security**, enable **Secret scanning**.
- [x] In **Settings > Advanced Security**, enable **Push protection** for
  detected secrets.
- [x] Keep `.github/dependabot.yml` on the default branch so Dependabot checks
  the root Python and npm manifests every week.

If a setting is unavailable, verify the repository visibility, organization
security policy, and GitHub Advanced Security entitlement before treating the
control as enabled.

## Verification

1. Open the repository's **Security** tab and confirm Dependabot and secret
   scanning views are available.
2. Review open Dependabot alerts by severity and runtime exposure. Record
   remediation or risk acceptance in a tracked issue.
3. Confirm **Push protection** is enabled in **Settings > Advanced Security**.
   Review bypass requests and audit-log events rather than using a permanent
   bypass.
4. After a blocked push, remove the secret from the commit history, revoke or
   rotate the exposed credential, and push the sanitized history. Do not bypass
   protection merely because the credential was revoked.

GitHub's API reported secret scanning, push protection, and Dependabot security
updates as enabled when this checklist was last verified.

Dependency audit results and remediation decisions are recorded in
[`dependency-audit-2026-07-28.md`](dependency-audit-2026-07-28.md).
