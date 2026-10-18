# Contributing

Issues and pull requests are welcome. Run the tests before sending a change:

```bash
python3 test_forge.py
```

Use [conventional commits](https://www.conventionalcommits.org): they drive releases.

## Releasing

Each push to `main` runs `.github/workflows/release.yml`. If `.claude-plugin/plugin.json` was
bumped by hand past the last `v*` tag, that version is released. Otherwise conventional
commits since the last tag that touch `skills/`, `scripts/` or `templates/` pick the bump:
`feat:` minor, `fix:`/`perf:`/`refactor:`/`docs:` patch, `type!:` or `BREAKING CHANGE` major;
other types release nothing. The workflow writes the version into `plugin.json` and
`skills/lobe-forge/SKILL.md`, commits, tags and publishes a GitHub release.
