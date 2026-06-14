# Contributing

Thank you for helping improve Total Recall.

## License Terms For Contributions

By submitting a pull request, patch, issue comment containing code, or any other
contribution intended for inclusion in this repository, you agree that:

- you have the right to submit the contribution;
- your contribution is licensed under `AGPL-3.0-or-later`;
- you grant Alex Covo and the Total Recall maintainers a perpetual, worldwide,
  non-exclusive, royalty-free license to use, reproduce, modify, distribute,
  sublicense, and relicense your contribution as part of Total Recall, including
  under separate commercial licenses.

Do not submit a contribution if you cannot agree to these terms.

## Security And Privacy

Do not include private Total Recall stores, real user memory, API keys, tokens,
passwords, production profile paths, or private handoff artifacts in issues,
pull requests, fixtures, or logs.

Run the privacy scan before opening a release-oriented pull request:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/privacy_scan.py
```

## Release Checks

Before a change is described as public-release ready, run the validation stack:

```bash
PYTHONDONTWRITEBYTECODE=1 python scripts/privacy_scan.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest -q
PYTHONDONTWRITEBYTECODE=1 ./scripts/install_smoke.sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python scripts/benchmark_total_recall.py --events 250 --queries 25
git diff --check
```

A clean `total-recall trust verify` for the exact commit is required before
tagging a stable release.
