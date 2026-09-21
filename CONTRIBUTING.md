# Contributing to JARVIS

Thanks for your interest in improving JARVIS.

The project is under active development and currently going through a provenance/licensing review. Until that review is complete, contributions should focus on changes that are clearly original to this repository and do not import third-party code without an explicit compatible license.

## Good contribution areas

Useful contributions include:

- bug fixes with a reproducible test case;
- Windows compatibility improvements;
- test coverage;
- security hardening;
- documentation;
- provider or connector fixes;
- performance improvements;
- accessibility and UI reliability;
- improvements to permission checks and auditability.

## Before opening a pull request

1. Open or reference an issue describing the problem.
2. Keep the change focused.
3. Avoid committing generated assets, local databases, credentials, API keys, or machine-specific configuration.
4. Add or update tests when behavior changes.
5. Run:

```bat
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

6. Explain what changed, why it changed, and how you verified it.

## Pull request checklist

- [ ] The change has a clear purpose.
- [ ] No secrets or private data are included.
- [ ] Existing behavior is preserved unless the PR intentionally changes it.
- [ ] Tests were run, or the PR explains why they could not be run.
- [ ] New third-party code or assets include their source and license information.
- [ ] Documentation is updated when user-facing behavior changes.

## Security issues

Please do not publish sensitive vulnerabilities as ordinary issues. Follow the process in [SECURITY.md](SECURITY.md).

## Style

Prefer small, reviewable changes. Avoid unrelated refactors inside a bug fix. Keep security-sensitive behavior explicit and auditable.

## Licensing note

Do not assume that this repository's current public visibility grants permission to reuse every file. The project is undergoing a provenance and licensing review. Contributions will only be merged under terms that are compatible with the final repository license and all upstream obligations.
