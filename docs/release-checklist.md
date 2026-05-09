# Share-Readiness Checklist

Before sharing a release outside the private repo:

- run `rg -n "/Users|\.hermes|OPENAI_API_KEY|token|password|secret" .`
- run `python -m pytest -q`
- verify `README.md` install steps from a fresh checkout
- verify the Hermes plugin loads from `hermes-plugin/total-recall`
- ensure no runtime store directories are tracked
- choose whether the repo should remain private or become public
- tag a release only after the above passes
