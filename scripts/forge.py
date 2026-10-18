#!/usr/bin/env python3
"""
forge.py — scaffold a lobe (LLM wiki) repo and install its <name>-* skills.

Commands:
  targets   List Claude installations (config dirs) that skills can be installed into
  create    Scaffold a lobe repo, render its skills, and symlink them into Claude installations
  install   Symlink an existing lobe repo's skills into Claude installations (e.g. a fresh clone)

Re-running create on an existing lobe repo is safe: existing files are kept, only missing
pieces are added. Nothing is ever overwritten or deleted.
"""

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

FORGE = Path(__file__).resolve().parent.parent
TEMPLATES = FORGE / "templates"
KINDS = ("add-raw", "ingest", "ask", "lint")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,29}$")

INDEX = """# {title} Wiki — Index

**Purpose**: {purpose}

---

## General

| Page | Description |
|---|---|
"""
LOG = """# {title} Wiki — Log

## {date} — Created

- Lobe `{name}` created by lobe-forge
"""


def say(msg: str) -> None:
    print(msg)


def fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def write_new(path: Path, text: str, made: list[str], root: Path) -> None:
    """Write only if the file does not exist yet."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    made.append(str(path.relative_to(root)))


def render(text: str, name: str, purpose: str) -> str:
    return text.replace("{{NAME}}", name).replace("{{PURPOSE}}", purpose)


def candidate_targets() -> list[Path]:
    home = Path.home()
    dirs = [home / ".claude"] + sorted(home.glob(".claude-*"))
    return [d for d in dirs if d.is_dir() and ((d / "settings.json").exists() or (d / "skills").is_dir())]


def link_skills(repo: Path, names: list[str], targets: list[Path]) -> int:
    """Symlink repo/skills/<skill> into <target>/skills/. Refuses to replace anything else."""
    errors = 0
    for t in targets:
        skills_dir = t.expanduser() / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill in names:
            src = repo / "skills" / skill
            dst = skills_dir / skill
            if dst.is_symlink() and dst.resolve() == src.resolve():
                say(f"  ok       {dst} (already linked)")
            elif dst.exists() or dst.is_symlink():
                print(f"  CONFLICT {dst} exists and is not a link to {src} — left untouched", file=sys.stderr)
                errors += 1
            else:
                dst.symlink_to(src)
                say(f"  linked   {dst} -> {src}")
    return 1 if errors else 0


def cmd_targets(_args) -> int:
    for d in candidate_targets():
        say(str(d))
    return 0


def cmd_create(args) -> int:
    name = args.name.strip().lower()
    if not NAME_RE.match(name):
        return fail(f"name '{args.name}' must be lowercase letters, digits and hyphens, start with a letter, max 30 chars")
    purpose = " ".join(args.purpose.split())
    if not purpose:
        return fail("purpose must not be empty")

    repo = Path(args.repo).expanduser().resolve()
    if repo.exists() and not repo.is_dir():
        return fail(f"{repo} is not a directory")
    repo.mkdir(parents=True, exist_ok=True)
    if subprocess.run(["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
                      capture_output=True).returncode != 0:
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        say(f"git init  {repo}")

    wiki = repo / "wiki"
    if wiki.is_dir() and not (wiki / "index.md").exists() and any(wiki.iterdir()):
        return fail(f"{wiki} already exists and is not a lobe wiki (no index.md) — use a different repo")
    existing = [p.name for p in (repo / "skills").glob("*-ask")] if (repo / "skills").is_dir() else []
    other = [s.removesuffix("-ask") for s in existing if s != f"{name}-ask"]
    if other:
        return fail(f"{repo} already holds lobe '{other[0]}' — one lobe per repo")

    title = name.replace("-", " ").title()
    today = datetime.date.today().isoformat()
    made: list[str] = []

    write_new(repo / "raw" / ".gitkeep", "", made, repo)
    write_new(wiki / "index.md", INDEX.format(title=title, purpose=purpose), made, repo)
    write_new(wiki / "log.md", LOG.format(title=title, date=today, name=name), made, repo)
    write_new(repo / "wiki_tool.py", (TEMPLATES / "wiki_tool.py").read_text(), made, repo)
    (repo / "wiki_tool.py").chmod(0o755)
    write_new(repo / "LOBE.md", render((TEMPLATES / "LOBE.md").read_text(), name, purpose), made, repo)
    for kind in KINDS:
        tpl = (TEMPLATES / "skills" / kind / "SKILL.md").read_text()
        write_new(repo / "skills" / f"{name}-{kind}" / "SKILL.md", render(tpl, name, purpose), made, repo)

    gitignore = repo / ".gitignore"
    lines = gitignore.read_text().splitlines() if gitignore.exists() else []
    if "catalog.jsonl" not in lines:
        gitignore.write_text("\n".join(lines + ["catalog.jsonl"]) + "\n")
        made.append(".gitignore (catalog.jsonl)")

    say(f"\nLobe '{name}' at {repo}")
    say("Created:" if made else "Nothing new — lobe already complete.")
    for m in made:
        say(f"  {m}")

    if not args.target:
        return 0
    say("\nInstalling skills:")
    return link_skills(repo, [f"{name}-{k}" for k in KINDS], [Path(t) for t in args.target])


def cmd_install(args) -> int:
    repo = Path(args.repo).expanduser().resolve()
    skills = sorted(p.parent.name for p in (repo / "skills").glob("*/SKILL.md"))
    if not skills:
        return fail(f"no skills/*/SKILL.md under {repo} — not a lobe repo")
    say(f"Installing {', '.join(skills)}:")
    return link_skills(repo, skills, [Path(t) for t in args.target])


def main() -> None:
    p = argparse.ArgumentParser(prog="forge.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("targets", help="List Claude config dirs")

    c = sub.add_parser("create", help="Scaffold a lobe repo and install its skills")
    c.add_argument("--name", required=True, help="Lobe name, e.g. personal -> personal-ask, personal-ingest, …")
    c.add_argument("--purpose", required=True, help="One line: what this lobe is for")
    c.add_argument("--repo", required=True, help="Local path of the wiki repo (created and git-initialised if missing)")
    c.add_argument("--target", action="append", default=[], help="Claude config dir to install into (repeatable)")

    i = sub.add_parser("install", help="Symlink an existing lobe repo's skills")
    i.add_argument("--repo", required=True)
    i.add_argument("--target", action="append", required=True)

    args = p.parse_args()
    sys.exit({"targets": cmd_targets, "create": cmd_create, "install": cmd_install}[args.command](args))


if __name__ == "__main__":
    main()
