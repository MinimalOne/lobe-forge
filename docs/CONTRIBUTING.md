# Contributing

Issues and pull requests are welcome. Run the tests before sending a change:

```bash
python3 test_forge.py
```

Use [conventional commits](https://www.conventionalcommits.org): they drive releases.

## Releasing

Each push to `main` runs `.github/workflows/release.yml`. If `.claude-plugin/plugin.json` was
bumped by hand past the last `v*` tag, that version is released. Otherwise conventional
commits since the last tag that touch `skills/`, `scripts/`, `templates/`, `server/` or
`openapi.yaml` pick the bump: `feat:` minor, `fix:`/`perf:`/`refactor:`/`docs:` patch,
`type!:` or `BREAKING CHANGE` major; other types release nothing. Changes to `docs/` or
`README.md` alone never release. The workflow writes the version into `plugin.json`,
`skills/lobe-forge/SKILL.md` and `openapi.yaml`, commits, tags and publishes a GitHub release.
