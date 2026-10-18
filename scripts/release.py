#!/usr/bin/env python3
"""
release.py — pick the next plugin version and write it into every file that carries it.

Run by .github/workflows/release.yml on each push to main. When a release is due it updates
the files, writes release notes to --notes and prints `version=X.Y.Z` (for $GITHUB_OUTPUT);
otherwise it prints nothing.

  plugin.json bumped by hand past the last v* tag  ->  release that version
  otherwise, conventional commits since the last tag that touch shipped files:
    type!: or BREAKING CHANGE  ->  major     feat:  ->  minor     fix: perf: refactor: docs:  ->  patch
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / ".claude-plugin" / "plugin.json"
SKILL = ROOT / "skills" / "lobe-forge" / "SKILL.md"
SHIPPED = ["skills", "scripts", "templates"]  # what an installed plugin runs


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout


def parse(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in version.split("."))


def bump(version: str, commits: list[tuple[str, str]]) -> str | None:
    """Next version for these (subject, body) commits, or None if none is releasable."""
    level = 0
    for subject, body in commits:
        m = re.match(r"(\w+)(\([^)]*\))?(!)?:", subject)
        if not m:
            continue
        if m.group(3) or "BREAKING CHANGE" in body:
            level = max(level, 3)
        elif m.group(1) == "feat":
            level = max(level, 2)
        elif m.group(1) in ("fix", "perf", "refactor", "docs"):
            level = max(level, 1)
    if not level:
        return None
    major, minor, patch = parse(version)
    return {3: f"{major + 1}.0.0", 2: f"{major}.{minor + 1}.0", 1: f"{major}.{minor}.{patch + 1}"}[level]


def commits_since(tag: str | None, *paths: str) -> list[tuple[str, str]]:
    log = git("log", "--format=%s%x1f%b%x1e", f"{tag}..HEAD" if tag else "HEAD", "--", *paths)
    # not e.strip(): Python counts \x1f as whitespace and would drop it before an empty body
    return [(s, b) for s, _, b in (e.strip("\n").partition("\x1f") for e in log.split("\x1e")) if s]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--notes", required=True, help="File to write the release notes to")
    args = p.parse_args()

    current = json.loads(PLUGIN.read_text())["version"]
    tags = git("tag", "--list", "v*", "--sort=-v:refname").split()
    last = tags[0] if tags else None
    if last is None or parse(current) > parse(last[1:]):
        version = current  # bumped by hand
    else:
        version = bump(last[1:], commits_since(last, *SHIPPED))
    if not version:
        return

    PLUGIN.write_text(re.sub(r'("version":\s*")[^"]+(")', rf"\g<1>{version}\2", PLUGIN.read_text(), count=1))
    SKILL.write_text(re.sub(r"^version: .*$", f"version: {version}", SKILL.read_text(), count=1, flags=re.M))
    notes = [f"- {s}" for s, _ in commits_since(last) if not s.startswith("chore: release v")]
    Path(args.notes).write_text("\n".join(notes) + "\n")
    print(f"version={version}")


if __name__ == "__main__":
    main()
