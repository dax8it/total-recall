# Security Policy

Total Recall stores continuity memory, checkpoints, device identity, and backup
metadata. Treat privacy and integrity issues as security issues.

Please do not disclose vulnerabilities publicly before the maintainer has had a
reasonable chance to respond.

If GitHub Security Advisories are available for this repository, use a private
advisory. Otherwise, open a minimal public issue asking for a private security
contact without including exploit details, secrets, private memory, or runtime
artifacts.

## What To Report

- secret, token, passphrase, or private-memory leaks;
- ways to bypass verification before rehydrate;
- tampering that is not detected by ledger, checkpoint, or anchor checks;
- unsafe restore, backup, handoff, or federation behavior;
- prompt-injection paths that can turn memory evidence into instructions.

## Public Fixtures

Use synthetic examples only. Redacted test strings are acceptable when they are
obviously fake and covered by tests.
