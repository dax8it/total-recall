# Public Launch Readiness

Total Recall can be shared publicly as an open source project under the
`AGPL-3.0-or-later` license with a separate commercial licensing path.

## License Posture

- Open source use: GNU Affero General Public License, version 3 or later.
- Proprietary, closed-source, hosted, embedded, resale, or non-AGPL use:
  requires a separate written commercial license.
- Attribution: preserve `NOTICE`, copyright notices, and the original repository
  reference.
- Trademark: modified products and services must not imply that they are the
  official Total Recall project.
- Contributions: contributors grant rights needed for AGPL and commercial
  dual-licensing through `CONTRIBUTING.md`.

## Public Launch Checklist

Run these before changing repository visibility or tagging a release:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/privacy_scan.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
PYTHONDONTWRITEBYTECODE=1 ./scripts/install_smoke.sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/benchmark_total_recall.py --events 250 --queries 25
git diff --check
```

Also verify the items in `docs/release-checklist.md`.

## Visibility Guidance

It is fine to make the repository public after the launch checklist passes and
the maintainer confirms the GitHub repository has no private runtime stores,
profile-specific artifacts, secrets, or unpublished commercial terms.

Tag a stable release only after the trust gate and release checklist pass for
the exact commit being tagged.
