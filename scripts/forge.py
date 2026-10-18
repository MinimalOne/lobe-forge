#!/usr/bin/env python3
"""
forge.py — scaffold or adopt a lobe (LLM wiki) and install its <name>-* skills.

Commands:
  targets   List Claude installations (config dirs) that skills can be installed into
  scan      Find existing LLM wikis (llmwiki/ or wiki/ with an index.md) under a directory
  create    Create a lobe, or adopt an existing wiki, and symlink its skills into Claude
  install   Symlink an existing lobe's skills into Claude installations (e.g. a fresh clone)
  upgrade   Re-render an existing lobe's generated files from the current templates

Two modes for create:
  in place  (default)   lobe files (skills/, wikictl, wiki_tool.py, LOBE.md) go into the repo
  external  (--home D)  lobe files go into D, with D/repo -> the wiki repo; the repo is not touched

Re-running create is safe: existing files are kept, only missing pieces are added. Nothing is
deleted. Only `upgrade --apply` overwrites, and only the generated files (wiki_tool.py, wikictl,
LOBE.md, skills/<name>-*/SKILL.md) — never wiki pages or sources.
"""

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

FORGE = Path(__file__).resolve().parent.parent
TEMPLATES = FORGE / "templates"
KINDS = ("add-raw", "ingest", "ask", "lint")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,29}$")
WIKI_NAMES = ("llmwiki", "wiki")
RAW_FOR = {"llmwiki": "docs", "wiki": "raw"}  # conventional raw dir per layout
SKIP_DIRS = {".git", "node_modules", "vendor", ".venv", "venv", "__pycache__", ".terraform", "dist", "build"}
ADOPTED_HOME = Path.home() / "lobes" / "_adopted"

COMMIT_IN_PLACE = "commit wiki changes as shown in the steps below; ask before pushing."
COMMIT_EXTERNAL = ("this wiki lives in a repo that is not a personal lobe repo — before every commit, "
                   "show `git -C \"$REPO\" diff --cached --stat` and ask; never push unless asked.")

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


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def write_new(path: Path, text: str, made: list[str], root: Path, mode: int | None = None) -> None:
    """Write only if the file does not exist yet."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mode:
        path.chmod(mode)
    made.append(str(path.relative_to(root)))


def render(text: str, **values: str) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def generated_files(name: str, purpose: str, wiki_dir: str, raw_dir: str, external: bool) -> dict[str, tuple[str, int | None]]:
    """Every file forge renders into a lobe home: relative path -> (text, mode)."""
    values = dict(NAME=name, PURPOSE=purpose, WIKI=wiki_dir, RAW=raw_dir,
                  REPO_LINK="repo" if external else ".",
                  COMMIT_RULE=COMMIT_EXTERNAL if external else COMMIT_IN_PLACE)
    files = {
        "wiki_tool.py": ((TEMPLATES / "wiki_tool.py").read_text(), 0o755),
        "wikictl": (render((TEMPLATES / "wikictl").read_text(), **values), 0o755),
        "LOBE.md": (render((TEMPLATES / "LOBE.md").read_text(), **values), None),
    }
    for kind in KINDS:
        tpl = (TEMPLATES / "skills" / kind / "SKILL.md").read_text()
        files[f"skills/{name}-{kind}/SKILL.md"] = (render(tpl, **values), None)
    return files


def lobe_settings(lobe: Path) -> tuple[str, str, str, str, bool] | str:
    """(name, purpose, wiki_dir, raw_dir, external) read back from a generated lobe, or an error."""
    asks = sorted(p.parent.name for p in lobe.glob("skills/*-ask/SKILL.md"))
    if len(asks) != 1 or not (lobe / "wikictl").is_file() or not (lobe / "LOBE.md").is_file():
        return f"{lobe} is not a lobe repo or lobe home (needs wikictl, LOBE.md and one skills/*-ask)"
    purpose = re.search(r"^\*\*Purpose\*\*: (.+)$", (lobe / "LOBE.md").read_text(), re.MULTILINE)
    wikictl = (lobe / "wikictl").read_text()
    wiki = re.search(r'WIKI_DIR="([^"]+)"', wikictl)
    raw = re.search(r'WIKI_RAW_DIR="([^"]+)"', wikictl)
    if not (purpose and wiki and raw):
        return f"cannot read the purpose from LOBE.md or the folders from wikictl in {lobe}"
    return asks[0].removesuffix("-ask"), purpose.group(1).strip(), wiki.group(1), raw.group(1), (lobe / "repo").is_symlink()


def candidate_targets() -> list[Path]:
    home = Path.home()
    dirs = [home / ".claude"] + sorted(home.glob(".claude-*"))
    return [d for d in dirs if d.is_dir() and ((d / "settings.json").exists() or (d / "skills").is_dir())]


def find_wikis(root: Path, depth: int) -> list[Path]:
    """Directories named llmwiki/ or wiki/ that contain an index.md, up to `depth` levels below root."""
    found = []
    base = len(root.parts)
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and len(here.parts) - base < depth]
        if here.name in WIKI_NAMES and (here / "index.md").is_file():
            found.append(here)
            dirnames[:] = []  # a wiki's subfolders are pages, not more wikis
    return sorted(found)


def wiki_skills_in(repo: Path) -> list[str]:
    """*-ask skills a repo already ships (lobe-forge skills/, plugin skills, project skills) —
    a sign that something already manages its wiki."""
    pats = ["skills/*-ask/SKILL.md", "plugins/*/skills/*-ask/SKILL.md", ".claude/skills/*-ask/SKILL.md"]
    return sorted({p.parent.name for pat in pats for p in repo.glob(pat)})


def adopted_homes_for(repo: Path) -> list[str]:
    if not ADOPTED_HOME.is_dir():
        return []
    return sorted(h.name for h in ADOPTED_HOME.iterdir()
                  if (h / "repo").is_symlink() and (h / "repo").resolve() == repo.resolve())


def link_skills(lobe: Path, names: list[str], targets: list[Path]) -> int:
    """Symlink lobe/skills/<skill> into <target>/skills/. Refuses to replace anything else."""
    errors = 0
    for t in targets:
        skills_dir = t.expanduser() / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        for skill in names:
            src = lobe / "skills" / skill
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


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_targets(_args) -> int:
    for d in candidate_targets():
        say(str(d))
    return 0


def cmd_scan(args) -> int:
    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        return fail(f"{root} is not a directory")
    wikis = find_wikis(root, args.depth)
    if not wikis:
        say(f"No LLM wikis found under {root}")
        return 0
    for w in wikis:
        repo = Path(git(w, "rev-parse", "--show-toplevel") or w.parent)
        pages = [p for p in w.glob("*.md") if p.name not in ("index.md", "log.md")]
        legacy = sum(1 for p in pages if not p.read_text(errors="replace").startswith("---\n"))
        raw = RAW_FOR[w.name] if (w.parent / RAW_FOR[w.name]).is_dir() else "?"
        managed = wiki_skills_in(repo) + [f"adopted:{h}" for h in adopted_homes_for(repo)]
        say(f"{w}")
        say(f"  repo={repo}  wiki_dir={w.relative_to(repo)}  raw_dir={raw}  pages={len(pages)}  legacy={legacy}")
        say(f"  remote={git(repo, 'remote', 'get-url', 'origin') or '-'}")
        say(f"  managed_by={','.join(managed) or '-'}")
    return 0


def cmd_create(args) -> int:
    name = args.name.strip().lower()
    if not NAME_RE.match(name):
        return fail(f"name '{args.name}' must be lowercase letters, digits and hyphens, start with a letter, max 30 chars")
    purpose = " ".join(args.purpose.split())
    if not purpose:
        return fail("purpose must not be empty")
    for d in (args.wiki_dir, args.raw_dir):
        if d.startswith("/") or ".." in Path(d).parts:
            return fail(f"--wiki-dir/--raw-dir must be relative to the repo: {d}")

    repo = Path(args.repo).expanduser().resolve()
    external = args.home is not None
    if external:
        if not repo.is_dir():
            return fail(f"{repo} does not exist — external mode only adopts existing repos")
        lobe = Path(args.home).expanduser().resolve()
        if lobe == repo or repo in lobe.parents:
            return fail("--home must be outside the wiki repo")
    else:
        if repo.exists() and not repo.is_dir():
            return fail(f"{repo} is not a directory")
        repo.mkdir(parents=True, exist_ok=True)
        if not git(repo, "rev-parse", "--is-inside-work-tree"):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            say(f"git init  {repo}")
        lobe = repo

    wiki = repo / args.wiki_dir
    if wiki.is_dir() and not (wiki / "index.md").exists() and any(wiki.iterdir()):
        return fail(f"{wiki} exists and is not an LLM wiki (no index.md) — pick another --wiki-dir or repo")
    if not wiki.is_dir():
        others = [w for w in find_wikis(repo, 3) if w != wiki]
        if others:
            rel = others[0].relative_to(repo)
            return fail(f"{repo} already has a wiki at {rel}/ — to adopt it pass --wiki-dir {rel}"
                        f" --raw-dir {RAW_FOR.get(others[0].name, 'raw')}")
        if external:
            return fail(f"{wiki} does not exist — external mode adopts an existing wiki only")
    existing = [p.name.removesuffix("-ask") for p in (lobe / "skills").glob("*-ask")] if (lobe / "skills").is_dir() else []
    other = [s for s in existing if s != name]
    if other:
        return fail(f"{lobe} already holds lobe '{other[0]}' — one lobe per repo or home")

    title = name.replace("-", " ").title()
    made: list[str] = []

    if external:
        lobe.mkdir(parents=True, exist_ok=True)
        link = lobe / "repo"
        if link.is_symlink() and link.resolve() != repo:
            return fail(f"{link} already points to {link.resolve()}")
        if not link.exists():
            link.symlink_to(repo)
            made.append("repo -> " + str(repo))
    else:
        # wiki content is only scaffolded for a lobe repo we own; an adopted wiki keeps what it has
        if not (repo / args.raw_dir).is_dir():
            write_new(repo / args.raw_dir / ".gitkeep", "", made, repo)
        write_new(wiki / "index.md", INDEX.format(title=title, purpose=purpose), made, repo)
        write_new(wiki / "log.md", LOG.format(title=title, date=datetime.date.today().isoformat(), name=name),
                  made, repo)
        gitignore = repo / ".gitignore"
        lines = gitignore.read_text().splitlines() if gitignore.exists() else []
        if "catalog.jsonl" not in lines:
            gitignore.write_text("\n".join(lines + ["catalog.jsonl"]) + "\n")
            made.append(".gitignore (catalog.jsonl)")

    for rel, (text, mode) in generated_files(name, purpose, args.wiki_dir, args.raw_dir, external).items():
        write_new(lobe / rel, text, made, lobe, mode)

    say(f"\nLobe '{name}' ({'external home ' + str(lobe) if external else 'in place'}) for {repo}")
    say(f"  wiki: {args.wiki_dir}/   raw: {args.raw_dir}/")
    say("Created:" if made else "Nothing new — lobe already complete.")
    for m in made:
        say(f"  {m}")

    if not args.target:
        return 0
    say("\nInstalling skills:")
    return link_skills(lobe, [f"{name}-{k}" for k in KINDS], [Path(t) for t in args.target])


def cmd_install(args) -> int:
    lobe = Path(args.repo).expanduser().resolve()
    skills = sorted(p.parent.name for p in (lobe / "skills").glob("*/SKILL.md"))
    if not skills or not (lobe / "wikictl").exists():
        return fail(f"{lobe} has no wikictl + skills/*/SKILL.md — not a lobe repo or lobe home")
    say(f"Installing {', '.join(skills)}:")
    return link_skills(lobe, skills, [Path(t) for t in args.target])


def cmd_upgrade(args) -> int:
    lobe = Path(args.repo).expanduser().resolve()
    settings = lobe_settings(lobe)
    if isinstance(settings, str):
        return fail(settings)
    name, _, _, _, external = settings
    files = generated_files(*settings)
    stale = [rel for rel, (text, _) in files.items()
             if not (lobe / rel).is_file() or (lobe / rel).read_text() != text]
    say(f"Lobe '{name}' ({'external home' if external else 'in place'}) at {lobe}")
    if not stale:
        say("Up to date with this lobe-forge's templates.")
        return 0
    for rel in stale:
        say(f"  {'update' if (lobe / rel).exists() else 'add   '}  {rel}")
    if not args.apply:
        say("\nDry run — rerun with --apply to write. Wiki pages and sources are never touched.")
        return 0

    if not external:
        # the previous version must be in git, so an overwritten local edit can be recovered
        dirty = git(lobe, "status", "--porcelain", "--", *stale)
        if dirty:
            return fail(f"uncommitted changes in files upgrade would overwrite — commit or stash first:\n{dirty}")
    new_skills = [rel for rel in stale if rel.startswith("skills/") and not (lobe / rel).exists()]
    for rel in stale:
        path = lobe / rel
        text, mode = files[rel]
        if external and path.exists():
            path.with_name(path.name + ".bak").write_bytes(path.read_bytes())  # home is not in git
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if mode:
            path.chmod(mode)
    say(f"\nUpgraded {len(stale)} file(s).")
    if external:
        say("Previous versions saved next to them as *.bak.")
    else:
        say(f'Review and commit: git -C "{lobe}" diff --stat && git -C "{lobe}" add {" ".join(stale)} '
            f'&& git -C "{lobe}" commit -m "lobe: upgrade {name} tooling"')
    if new_skills:
        say(f"New skills added — link them with: forge.py install --repo {lobe} --target <claude dir>")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="forge.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("targets", help="List Claude config dirs")

    s = sub.add_parser("scan", help="Find existing LLM wikis under a directory")
    s.add_argument("dir")
    s.add_argument("--depth", type=int, default=4)

    c = sub.add_parser("create", help="Create or adopt a lobe and install its skills")
    c.add_argument("--name", required=True, help="Lobe name, e.g. personal -> personal-ask, personal-ingest, …")
    c.add_argument("--purpose", required=True, help="One line: what this lobe is for")
    c.add_argument("--repo", required=True, help="Local path of the wiki repo (created if missing, in place only)")
    c.add_argument("--wiki-dir", default="wiki", help="Wiki folder relative to the repo (llmwiki for lobe layout)")
    c.add_argument("--raw-dir", default="raw", help="Raw sources folder relative to the repo (docs for lobe layout)")
    c.add_argument("--home", help=f"External mode: put lobe files here, not in the repo (e.g. {ADOPTED_HOME}/<name>)")
    c.add_argument("--target", action="append", default=[], help="Claude config dir to install into (repeatable)")

    i = sub.add_parser("install", help="Symlink an existing lobe's skills")
    i.add_argument("--repo", required=True, help="Lobe repo (in place) or lobe home (external)")
    i.add_argument("--target", action="append", required=True)

    u = sub.add_parser("upgrade", help="Re-render a lobe's generated files from the current templates")
    u.add_argument("--repo", required=True, help="Lobe repo (in place) or lobe home (external)")
    u.add_argument("--apply", action="store_true", help="Write the files (default: dry run)")

    args = p.parse_args()
    cmds = {"targets": cmd_targets, "scan": cmd_scan, "create": cmd_create, "install": cmd_install,
            "upgrade": cmd_upgrade}
    sys.exit(cmds[args.command](args))


if __name__ == "__main__":
    main()
