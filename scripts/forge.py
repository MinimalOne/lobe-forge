#!/usr/bin/env python3
"""
forge.py — scaffold or adopt a lobe (LLM wiki) and install its <name>-* skills.

Commands:
  targets   List Claude installations (config dirs) that skills can be installed into
  scan      Find existing LLM wikis (llmwiki/ or wiki/ with an index.md) under a directory
  create    Create a lobe, or adopt an existing wiki, and symlink its skills into Claude
  install   Symlink an existing lobe's skills into Claude installations (e.g. a fresh clone)
  upgrade   Re-render a lobe's generated files from the current templates (--all: every installed
            lobe) and link forge skills missing from Claude dirs that already have the lobe
  uninstall Remove a lobe's skill links (and, with --purge, an external home's generated files)

Two modes for create:
  in place  (default)   lobe files (skills/, wikictl, wiki_tool.py, LOBE.md) go into the repo
  external  (--home D)  lobe files go into D, with D/repo -> the wiki repo; the repo is not touched

Re-running create is safe: existing files are kept, only missing pieces are added. Only
`upgrade --apply` overwrites, and `uninstall` deletes, and only what forge made: the generated
files (wiki_tool.py, wikictl, LOBE.md, skills/<name>-*/SKILL.md) and skill links — never wiki
pages or sources.
"""

import argparse
import datetime
import json
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


def generated_files(name: str, purpose: str, wiki_dir: str, raw_dir: str, external: bool,
                    origin: str) -> dict[str, tuple[str, int | None]]:
    """Every file forge renders into a lobe home: relative path -> (text, mode)."""
    values = dict(NAME=name, PURPOSE=purpose, WIKI=wiki_dir, RAW=raw_dir, ORIGIN=origin,
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


def lobe_settings(lobe: Path) -> tuple[str, str, str, str, bool, str] | str:
    """(name, purpose, wiki_dir, raw_dir, external, origin) read back from a generated lobe, or an error."""
    asks = sorted(p.parent.name for p in lobe.glob("skills/*-ask/SKILL.md"))
    if len(asks) != 1 or not (lobe / "wikictl").is_file() or not (lobe / "LOBE.md").is_file():
        return f"{lobe} is not a lobe repo or lobe home (needs wikictl, LOBE.md and one skills/*-ask)"
    lobe_md = (lobe / "LOBE.md").read_text()
    purpose = re.search(r"^\*\*Purpose\*\*: (.+)$", lobe_md, re.MULTILINE)
    origin = re.search(r"^\*\*Origin\*\*: (.+)$", lobe_md, re.MULTILINE)  # absent before v1.3.0
    wikictl = (lobe / "wikictl").read_text()
    wiki = re.search(r'WIKI_DIR="([^"]+)"', wikictl)
    raw = re.search(r'WIKI_RAW_DIR="([^"]+)"', wikictl)
    if not (purpose and wiki and raw):
        return f"cannot read the purpose from LOBE.md or the folders from wikictl in {lobe}"
    return (asks[0].removesuffix("-ask"), purpose.group(1).strip(), wiki.group(1), raw.group(1),
            (lobe / "repo").is_symlink(), origin.group(1).strip() if origin else "unknown")


def remote_origin(repo: Path) -> str:
    """'github.com/<owner>/<repo>' for a GitHub remote, the raw URL otherwise."""
    url = git(repo, "remote", "get-url", "origin")
    if not url:
        return "local repo, no remote"
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return f"github.com/{m.group(1)}" if m else url


def tracked_state(repo: Path, rel: str) -> str:
    if not git(repo, "rev-parse", "--is-inside-work-tree"):
        return "no-git"
    if git(repo, "ls-files", "--", rel):
        return "yes"
    rule = git(repo, "check-ignore", "-v", rel)  # "<source>:<line>:<pattern>\t<path>"
    return f"ignored({rule.split(chr(9))[0]})" if rule else "untracked"


def installed_skill_files() -> list[tuple[str, Path]]:
    """(label, SKILL.md) for every skill in each Claude config dir: skills/ and installed plugins."""
    out = []
    for t in candidate_targets():
        out += [(f"{t.name}:{p.parent.name}", p) for p in sorted(t.glob("skills/*/SKILL.md"))]
        try:
            plugins = json.loads((t / "plugins" / "installed_plugins.json").read_text()).get("plugins", {})
        except (OSError, ValueError, AttributeError):
            plugins = {}
        for key, installs in plugins.items():
            for inst in installs if isinstance(installs, list) else []:
                path = inst.get("installPath") if isinstance(inst, dict) else None
                if path:
                    out += [(f"{t.name}:{key.split('@')[0]}:{p.parent.name}", p)
                            for p in sorted(Path(path).glob("skills/*/SKILL.md"))]
    return out


def other_wiki_skills(repo: Path, wiki_dir: str) -> list[str]:
    """Installed skills, outside this repo, that name this repo or a <wiki_dir>/index.md — they
    would compete with a new lobe's skills. lobe-forge skills (they run wikictl) are skipped:
    managed_by already reports those."""
    paths = [str(repo)]
    if Path.home().resolve() in repo.parents:  # skills usually spell paths as ~/...
        paths.append("~/" + str(repo.relative_to(Path.home().resolve())))
    pat = re.compile("|".join(re.escape(p) + r"(?![\w.-])" for p in paths)
                     + r"|(?<![\w-])" + re.escape(wiki_dir) + r"/index\.md")
    hits = set()
    for label, p in installed_skill_files():
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        if "wikictl" not in text and pat.search(text):
            hits.add(label)
    return sorted(hits)


def raw_authors(repo: Path, rel: str) -> int:
    return len(set(git(repo, "log", "--format=%ae", "--", rel).split()))


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
        rel = str(w.relative_to(repo))
        authors = raw_authors(repo, raw) if raw != "?" else 0
        say(f"{w}")
        say(f"  repo={repo}  wiki_dir={rel}  raw_dir={raw}  pages={len(pages)}  legacy={legacy}")
        say(f"  remote={git(repo, 'remote', 'get-url', 'origin') or '-'}")
        say(f"  tracked={tracked_state(repo, rel)}  raw_authors={authors if raw != '?' else '-'}")
        say(f"  managed_by={','.join(managed) or '-'}")
        say(f"  other_skills={','.join(other_wiki_skills(repo, rel)) or '-'}")
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
    origin = args.origin or (remote_origin(repo) if wiki.is_dir() else "new")
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

    for rel, (text, mode) in generated_files(name, purpose, args.wiki_dir, args.raw_dir, external, origin).items():
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


def links_to(lobe: Path, target: Path) -> list[Path]:
    """Skill links in a Claude config dir that point into this lobe's skills/."""
    skills = (lobe / "skills").resolve()
    return [l for l in sorted((target / "skills").glob("*"))
            if l.is_symlink() and Path(os.path.realpath(l)).parent == skills]


def installed_lobes() -> list[Path]:
    """Lobes with a skill linked into any Claude config dir, found by walking up from each link."""
    found = set()
    for t in candidate_targets():
        for link in (t / "skills").glob("*"):
            if not link.is_symlink():
                continue
            # lobe/skills/<s>, or lobe/plugins/<p>/skills/<s> when lobe/skills is a symlink there
            for up in list(Path(os.path.realpath(link)).parents)[1:4]:
                if not isinstance(lobe_settings(up), str):
                    found.add(up)
                    break
    return sorted(found)


def upgrade_lobe(lobe: Path, apply: bool, origin: str | None = None) -> int:
    settings = lobe_settings(lobe)
    if isinstance(settings, str):
        return fail(settings)
    if origin:
        settings = (*settings[:5], " ".join(origin.split()))
    name, _, _, _, external, _ = settings
    files = generated_files(*settings)
    stale = [rel for rel, (text, _) in files.items()
             if not (lobe / rel).is_file() or (lobe / rel).read_text() != text]
    # a forge skill rewritten by hand (it no longer runs wikictl) belongs to the user now
    kept = [rel for rel in stale if rel.startswith("skills/") and (lobe / rel).is_file()
            and "wikictl" not in (lobe / rel).read_text()]
    stale = [rel for rel in stale if rel not in kept]
    # forge's own skills missing from a Claude dir that already has this lobe installed
    targets = [t for t in candidate_targets() if links_to(lobe, t)]
    unlinked = {t: missing for t in targets
                if (missing := [f"{name}-{k}" for k in KINDS
                                if not (t / "skills" / f"{name}-{k}").exists()
                                and not (t / "skills" / f"{name}-{k}").is_symlink()])}
    say(f"Lobe '{name}' ({'external home' if external else 'in place'}) at {lobe}")
    for rel in kept:
        say(f"  keep    {rel} (hand-written: does not run wikictl)")
    if not stale and not unlinked:
        say("Up to date with this lobe-forge's templates.")
        return 0
    for rel in stale:
        say(f"  {'update' if (lobe / rel).exists() else 'add   '}  {rel}")
    for t, missing in unlinked.items():
        for skill in missing:
            say(f"  link    {t / 'skills' / skill}")
    if not apply:
        say("\nDry run — rerun with --apply to write. Wiki pages and sources are never touched.")
        return 0

    if stale and not external:
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
    if stale:
        say(f"\nUpgraded {len(stale)} file(s).")
        if external:
            say("Previous versions saved next to them as *.bak.")
        else:
            say(f'Review and commit: git -C "{lobe}" diff --stat && git -C "{lobe}" add {" ".join(stale)} '
                f'&& git -C "{lobe}" commit -m "lobe: upgrade {name} tooling"')
    if new_skills and not targets:
        say(f"New skills added — link them with: forge.py install --repo {lobe} --target <claude dir>")
    errors = 0
    for t, missing in unlinked.items():
        errors |= link_skills(lobe, missing, [t])
    return errors


def cmd_upgrade(args) -> int:
    if not args.all:
        return upgrade_lobe(Path(args.repo).expanduser().resolve(), args.apply, args.origin)
    if args.origin:
        return fail("--origin is per lobe — use it with --repo, not --all")
    lobes = installed_lobes()
    if not lobes:
        say("No lobes installed in any Claude config dir.")
    errors = 0
    for lobe in lobes:
        errors |= upgrade_lobe(lobe, args.apply)
        say("")
    return errors


def cmd_uninstall(args) -> int:
    lobe = Path(args.repo).expanduser().resolve()
    settings = lobe_settings(lobe)
    if isinstance(settings, str):
        return fail(settings)
    name, *_, external, _ = settings
    doomed: list[Path] = []
    if args.purge:
        if not external:
            return fail("--purge is for external homes only; in place the lobe files are part of the repo "
                        f"— remove them with git rm in {lobe}")
        made = {lobe / rel for rel in generated_files(*settings)}
        made |= {p.with_name(p.name + ".bak") for p in made} | {lobe / "catalog.jsonl", lobe / "repo"}
        doomed = [p for p in made if p.is_symlink() or p.exists()]
        extra = [p for p in lobe.rglob("*") if not p.is_dir() and p not in made and lobe / "repo" not in p.parents]
        if extra:
            return fail(f"{lobe} holds files forge did not generate — nothing removed:\n"
                        + "\n".join(f"  {p.relative_to(lobe)}" for p in sorted(extra)))

    targets = [Path(t).expanduser() for t in args.target] or candidate_targets()
    removed = 0
    for t in targets:
        for link in links_to(lobe, t):
            link.unlink()
            say(f"  unlinked {link}")
            removed += 1
    say(f"Removed {removed} skill link(s) for lobe '{name}'.")

    if args.purge:
        for p in doomed:
            p.unlink()
        for d in sorted((p for p in lobe.rglob("*") if p.is_dir() and not p.is_symlink()), reverse=True):
            d.rmdir()  # only empty dirs remain: everything else was checked above
        lobe.rmdir()
        say(f"Purged external home {lobe} ({len(doomed)} file(s)); the wiki repo was not touched.")
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
    c.add_argument("--origin", help="Where the wiki content came from (default: new, or the repo's remote when adopting)")

    i = sub.add_parser("install", help="Symlink an existing lobe's skills")
    i.add_argument("--repo", required=True, help="Lobe repo (in place) or lobe home (external)")
    i.add_argument("--target", action="append", required=True)

    u = sub.add_parser("upgrade", help="Re-render a lobe's generated files from the current templates")
    which = u.add_mutually_exclusive_group(required=True)
    which.add_argument("--repo", help="Lobe repo (in place) or lobe home (external)")
    which.add_argument("--all", action="store_true", help="Every lobe with skills linked into a Claude config dir")
    u.add_argument("--apply", action="store_true", help="Write the files (default: dry run)")
    u.add_argument("--origin", help="Set where the wiki content came from (written to LOBE.md)")

    r = sub.add_parser("uninstall", help="Remove a lobe's skill links; --purge also deletes an external home")
    r.add_argument("--repo", required=True, help="Lobe repo (in place) or lobe home (external)")
    r.add_argument("--target", action="append", default=[], help="Claude config dir (default: all of them)")
    r.add_argument("--purge", action="store_true", help="External home only: delete its generated files")

    args = p.parse_args()
    cmds = {"targets": cmd_targets, "scan": cmd_scan, "create": cmd_create, "install": cmd_install,
            "upgrade": cmd_upgrade, "uninstall": cmd_uninstall}
    sys.exit(cmds[args.command](args))


if __name__ == "__main__":
    main()
