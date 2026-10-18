"""Smoke tests for forge.py: create, adopt (in place and external), scan, install. Run: python3 test_forge.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FORGE = Path(__file__).parent / "scripts" / "forge.py"


def run(*args, home=None):
    env = {**os.environ, **({"HOME": str(home)} if home else {})}
    r = subprocess.run([sys.executable, str(FORGE), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def sh(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)


def git_repo(path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], check=True)
    return path


LEGACY = "# Old Page\n\n**Summary**: Holiday.\n\n**Sources**: docs/trip.md\n\n**Last updated**: 2025-01-01\n\n---\n\nText.\n"

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    claude = d / "claude"

    # ── new lobe, in place ──────────────────────────────────────────────────
    repo = d / "lobes" / "personal"
    code, out = run("create", "--name", "personal", "--purpose", "My life: notes, plans.",
                    "--repo", str(repo), "--target", str(claude))
    assert code == 0, out
    for f in ["raw/.gitkeep", "wiki/index.md", "wiki/log.md", "wiki_tool.py", "wikictl", "LOBE.md", ".gitignore"]:
        assert (repo / f).exists(), f
    for k in ["add-raw", "ingest", "ask", "lint"]:
        skill = (repo / "skills" / f"personal-{k}" / "SKILL.md").read_text()
        assert f"name: personal-{k}\n" in skill and "{{" not in skill, k
        link = claude / "skills" / f"personal-{k}"
        assert link.is_symlink() and link.resolve() == (repo / "skills" / f"personal-{k}").resolve()
    assert "**Origin**: new\n" in (repo / "LOBE.md").read_text()

    # the header every generated skill runs resolves the repo through the install symlink
    base = claude / "skills" / "personal-ask"
    # a shell whose cd is aliased (zoxide, autoenv) must not break the header
    r = sh(f'cd() {{ echo "zoxide: no match found" >&2; return 1; }}; '
           f'SKILL_BASE_DIR="{base}"; W="$(builtin cd -P "${{SKILL_BASE_DIR}}/../.." && pwd)/wikictl"; '
           f'eval "$("$W" paths)"; echo "$REPO|$WIKI|$RAW"; "$W" sections; "$W" lint --strict >/dev/null; echo rc=$?')
    rp = repo.resolve()
    assert r.stdout.splitlines() == [f"{rp}|{rp}/wiki|{rp}/raw", "General", "rc=0"], r.stdout + r.stderr

    # idempotent, keeps edits
    (repo / "LOBE.md").write_text("edited")
    code, out = run("create", "--name", "personal", "--purpose", "x", "--repo", str(repo), "--target", str(claude))
    assert code == 0 and "Nothing new" in out and (repo / "LOBE.md").read_text() == "edited", out

    # refusals: second lobe in a repo, bad name, clobbering a foreign skill
    assert run("create", "--name", "family", "--purpose", "x", "--repo", str(repo))[0] == 1
    assert run("create", "--name", "Bad Name", "--purpose", "x", "--repo", str(d / "y"))[0] == 1
    (claude / "skills" / "work-ask").mkdir(parents=True)
    code, out = run("create", "--name", "work", "--purpose", "x", "--repo", str(d / "work"), "--target", str(claude))
    assert code == 1 and "CONFLICT" in out and not (claude / "skills" / "work-ask").is_symlink(), out

    # ── existing lobe-layout wiki (llmwiki/ + docs/) ────────────────────────
    files = {"docs/trip.md": "src", "llmwiki/index.md": "# Index\n\n## General\n\n| Page | Description |\n|---|---|\n| [[old-page]] | x |\n",
             "llmwiki/log.md": "# Log\n", "llmwiki/old-page.md": LEGACY, "main.go": "package main\n"}
    own = git_repo(d / "src" / "own", files)
    work = git_repo(d / "src" / "work", files)
    subprocess.run(["git", "-C", str(work), "remote", "add", "origin", "git@github.com:acme/work.git"], check=True)

    # guard: default --wiki-dir would create a second wiki beside llmwiki/
    code, out = run("create", "--name", "own", "--purpose", "x", "--repo", str(own))
    assert code == 1 and "--wiki-dir llmwiki --raw-dir docs" in out and not (own / "wiki").exists(), out

    # scan finds both, reports layout and legacy pages
    code, out = run("scan", str(d / "src"), home=d)
    assert code == 0 and out.count("wiki_dir=llmwiki  raw_dir=docs  pages=1  legacy=1") == 2, out

    # adopt in place: no wiki content rewritten, skills point at llmwiki/ + docs/
    code, out = run("create", "--name", "own", "--purpose", "My repo.", "--repo", str(own),
                    "--wiki-dir", "llmwiki", "--raw-dir", "docs", "--target", str(claude))
    assert code == 0, out
    assert (own / "llmwiki" / "old-page.md").read_text() == LEGACY and not (own / "docs" / ".gitkeep").exists()
    r = sh(f'"{own}/wikictl" search holiday')
    assert "[[old-page]]" in r.stdout, r.stdout + r.stderr
    assert "commit wiki changes" in (own / "skills" / "own-ingest" / "SKILL.md").read_text()
    assert "**Origin**: local repo, no remote\n" in (own / "LOBE.md").read_text()
    assert "H is not checked on legacy pages" in sh(f'"{own}/wikictl" lint').stdout

    # legacy Sources lines: commas inside one item, PR links, bare filenames that are not raw files
    (own / "docs" / "runbook.md").write_text("src")
    pr = "[#1](https://x/pull/1), [#2](https://x/pull/2)"
    mixed = (f"# Mixed; Page\n\n**Summary**: Uses \"quotes\" and a \\ backslash.\n\n"
             f"**Sources**: `data.tf`, `aurora.tf`; PRs {pr}; `docs/runbook.md`; `docs/gone.md` (deleted); "
             f"Slack (C1; thread 2)\n\n**Last updated**: 2026-10-12\n\n---\n\n## Body\n\nKeep [[old-page]] text.\n")
    nohead = "# No Header\n\nJust text.\n"
    (own / "llmwiki" / "mixed.md").write_text(mixed)
    (own / "llmwiki" / "nohead.md").write_text(nohead)
    lint = sh(f'"{own}/wikictl" build >/dev/null && cat "{own}/catalog.jsonl"').stdout
    entry = next(json.loads(l) for l in lint.splitlines() if '"llmwiki/mixed.md"' in l)
    assert entry["sources"] == ["docs/runbook.md"], entry  # not data.tf / PR fragments
    assert entry["provenance"] == ["`data.tf`, `aurora.tf`", f"PRs {pr}", "`docs/gone.md` (deleted)",
                                   "Slack (C1; thread 2)"], entry["provenance"]

    r = sh(f'"{own}/wikictl" convert-legacy')  # dry run writes nothing
    assert r.returncode == 1 and "would convert mixed.md" in r.stdout and "skip nohead.md" in r.stdout, r.stdout
    assert (own / "llmwiki" / "mixed.md").read_text() == mixed

    r = sh(f'"{own}/wikictl" convert-legacy --apply')
    assert "Converted 2 page(s); 1 skipped" in r.stdout, r.stdout
    assert (own / "llmwiki" / "nohead.md").read_text() == nohead
    new = (own / "llmwiki" / "mixed.md").read_text()
    assert new.startswith('---\ntitle: "Mixed; Page"\nsummary: "Uses \\"quotes\\" and a \\\\ backslash."\n'), new
    assert new.endswith("---\n\n# Mixed; Page\n\n## Body\n\nKeep [[old-page]] text.\n"), new
    assert (own / "llmwiki" / "old-page.md").read_text().startswith("---\ntitle: \"Old Page\"\n")
    (own / "llmwiki" / "nohead.md").unlink()
    r = sh(f'"{own}/wikictl" build && "{own}/wikictl" lint --strict && "{own}/wikictl" search quotes')
    assert r.returncode == 0 and "2 with frontmatter, 0 legacy" in r.stdout and "[[mixed]]" in r.stdout, r.stdout
    entry = next(json.loads(l) for l in (own / "catalog.jsonl").read_text().splitlines() if "mixed.md" in l)
    assert entry["summary"] == 'Uses "quotes" and a \\ backslash.' and entry["source_count"] == 1, entry
    r = sh(f'"{own}/wikictl" convert-legacy --apply')  # idempotent
    assert r.returncode == 0 and "Converted 0 page(s)" in r.stdout, r.stdout

    # every candidate query in the generated lint skill runs; a passed event date is flagged
    (own / "llmwiki" / "cutover.md").write_text(
        '---\ntitle: "Cutover"\nsummary: "Planned for 2026-01-15, review on 2999-01-01."\nsources: []\n'
        'source_count: 0\nprovenance:\n  - "chat"\nlast_updated: 2026-01-10\n---\n\n# Cutover\n')
    queries = [l for l in (own / "skills" / "own-lint" / "SKILL.md").read_text().splitlines() if l.startswith("python3 -c")]
    r = sh(f'"{own}/wikictl" build >/dev/null && builtin cd "{own}" && ' + " && ".join(queries))
    assert len(queries) == 3 and r.returncode == 0, r.stderr
    assert "llmwiki/cutover.md 2026-01-10 event 2026-01-15" in r.stdout and "2999" not in r.stdout, r.stdout

    # adopt external: the work repo is untouched, lobe home links to it
    home = d / "lobes" / "_adopted" / "work-wiki"
    code, out = run("create", "--name", "work-wiki", "--purpose", "Team repo.", "--repo", str(work),
                    "--wiki-dir", "llmwiki", "--raw-dir", "docs", "--home", str(home), "--target", str(claude))
    assert code == 0, out
    status = subprocess.run(["git", "-C", str(work), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status == "", status
    assert "**Origin**: github.com/acme/work\n" in (home / "LOBE.md").read_text()
    assert (home / "repo").resolve() == work.resolve()
    assert "never push unless asked" in (home / "skills" / "work-wiki-ingest" / "SKILL.md").read_text()
    base = claude / "skills" / "work-wiki-lint"
    r = sh(f'SKILL_BASE_DIR="{base}"; W="$(builtin cd -P "${{SKILL_BASE_DIR}}/../.." && pwd)/wikictl"; '
           f'eval "$("$W" paths)"; echo "$REPO"; "$W" build >/dev/null; ls "$LOBE"/catalog.jsonl')
    assert r.stdout.splitlines()[0] == str(work.resolve()) and r.returncode == 0, r.stdout + r.stderr
    assert subprocess.run(["git", "-C", str(work), "status", "--porcelain"], capture_output=True, text=True).stdout == ""

    # scan now reports which lobe manages each repo
    code, out = run("scan", str(d / "src"), home=d)
    assert "managed_by=own-ask" in out and "adopted:work-wiki" in out, out

    # external mode refuses a missing wiki and a home inside the repo
    assert run("create", "--name", "z", "--purpose", "x", "--repo", str(work), "--home", str(work / "h"),
               "--wiki-dir", "llmwiki", "--raw-dir", "docs")[0] == 1
    assert run("create", "--name", "z", "--purpose", "x", "--repo", str(work), "--home", str(d / "h2"))[0] == 1

    # upgrade: an in-place lobe built from older templates
    def commit(path):
        subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c"], check=True)

    fresh = {rel: (own / rel).read_text() for rel in ["wiki_tool.py", "wikictl", "LOBE.md", "skills/own-ask/SKILL.md"]}
    (own / "wiki_tool.py").write_text("# old\n")
    ask = own / "skills" / "own-ask" / "SKILL.md"
    ask.write_text(ask.read_text().replace("builtin cd -P", "cd -P"))
    (own / "skills" / "own-lint" / "SKILL.md").unlink()  # a skill kind added in a later release
    commit(own)
    pages = {p.name: p.read_text() for p in (own / "llmwiki").glob("*.md")}

    code, out = run("upgrade", "--repo", str(own))  # dry run
    assert code == 0 and "update  wiki_tool.py" in out and "update  skills/own-ask/SKILL.md" in out, out
    assert "add     skills/own-lint/SKILL.md" in out and "LOBE.md" not in out, out
    assert (own / "wiki_tool.py").read_text() == "# old\n"

    (own / "wiki_tool.py").write_text("# local edit\n")  # uncommitted edit blocks the overwrite
    code, out = run("upgrade", "--repo", str(own), "--apply")
    assert code == 1 and "commit or stash" in out and (own / "wiki_tool.py").read_text() == "# local edit\n", out
    subprocess.run(["git", "-C", str(own), "checkout", "-q", "--", "wiki_tool.py"], check=True)

    code, out = run("upgrade", "--repo", str(own), "--apply")
    assert code == 0 and "Upgraded 3 file(s)" in out and "New skills added" in out, out
    for rel, text in fresh.items():
        assert (own / rel).read_text() == text, rel
    assert os.access(own / "wiki_tool.py", os.X_OK)
    assert (own / "skills" / "own-lint" / "SKILL.md").is_file()
    assert {p.name: p.read_text() for p in (own / "llmwiki").glob("*.md")} == pages  # wiki untouched
    assert "Up to date" in run("upgrade", "--repo", str(own))[1]

    # upgrade: external home keeps a .bak (it is not in git); the wiki repo stays clean
    tool = home / "wiki_tool.py"
    tool.write_text("# old\n")
    code, out = run("upgrade", "--repo", str(home), "--apply")
    assert code == 0 and "external home" in out and (home / "wiki_tool.py.bak").read_text() == "# old\n", out
    assert tool.read_text() == fresh["wiki_tool.py"]
    assert f"REPO='{work.resolve()}'" in sh(f'"{home}/wikictl" paths').stdout  # still external, not "."
    assert subprocess.run(["git", "-C", str(work), "status", "--porcelain"], capture_output=True, text=True).stdout == ""

    # upgrade refuses what it cannot read back
    assert run("upgrade", "--repo", str(d / "src"))[0] == 1
    code, out = run("upgrade", "--repo", str(repo))  # LOBE.md was hand-edited to "edited" above
    assert code == 1 and "purpose" in out, out

    # install mode works for both kinds of lobe
    for lobe, skill in [(repo, "personal-ingest"), (home, "work-wiki-ask")]:
        code, out = run("install", "--repo", str(lobe), "--target", str(d / "other"))
        assert code == 0 and (d / "other" / "skills" / skill).is_symlink(), out

    # upgrade --origin records where moved content came from, and upgrade reads it back
    code, out = run("upgrade", "--repo", str(own), "--apply", "--origin", "github.com/acme/team-wiki")
    assert code == 0 and "**Origin**: github.com/acme/team-wiki\n" in (own / "LOBE.md").read_text(), out
    commit(own)
    assert "Up to date" in run("upgrade", "--repo", str(own))[1]

    # scan: tracking state, shared raw folder, and other installed skills that serve the wiki
    ign = git_repo(d / "src2" / "ign", {"docs/a.md": "x"})
    (ign / "docs" / "b.md").write_text("y")
    subprocess.run(["git", "-C", str(ign), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(ign), "-c", "user.email=b@b", "-c", "user.name=b", "commit", "-qm", "b"], check=True)
    (ign / ".git" / "info" / "exclude").write_text("llmwiki/\n")
    (ign / "llmwiki").mkdir()
    (ign / "llmwiki" / "index.md").write_text("# Index\n")
    cfg = d / ".claude"
    (cfg / "settings.json").parent.mkdir(parents=True, exist_ok=True)
    (cfg / "settings.json").write_text("{}")
    for skill, text in {"old-wiki": "updates llmwiki/index.md", "near": "updates wiki/index.md",
                        "gen-ask": "runs wikictl on llmwiki/index.md", "by-path": "reads ~/src2/ign/notes",
                        "prefix": "reads ~/src2/ign-old/notes"}.items():
        (cfg / "skills" / skill).mkdir(parents=True)
        (cfg / "skills" / skill / "SKILL.md").write_text(text)
    (d / "plug" / "skills" / "lobe-ask").mkdir(parents=True)
    (d / "plug" / "skills" / "lobe-ask" / "SKILL.md").write_text("answers from llmwiki/index.md")
    (cfg / "plugins").mkdir()
    (cfg / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {"lobe@mine": [{"installPath": str(d / "plug")}]}}))
    code, out = run("scan", str(d / "src2"), home=d)
    assert code == 0 and "tracked=ignored(.git/info/exclude:1:llmwiki/)  raw_authors=2" in out, out
    assert "other_skills=.claude:by-path,.claude:lobe:lobe-ask,.claude:old-wiki\n" in out, out
    code, out = run("scan", str(own), home=d)
    assert "tracked=yes  raw_authors=1" in out, out

    # upgrade --all: finds lobes from their links (also via a skills/ symlink, as some adopted repos have), touches
    # only forge's own skills, and links forge skills missing from a Claude dir that has the lobe
    assert run("install", "--repo", str(own), "--target", str(cfg), home=d)[0] == 0
    (cfg / "skills" / "own-lint").unlink()
    (own / "skills" / "own-custom").mkdir()
    (own / "skills" / "own-custom" / "SKILL.md").write_text("mine")
    ask.write_text("# old\n")
    commit(own)
    kdb = git_repo(d / "src3" / "kdb", {"wiki/index.md": "# I\n"})
    assert run("create", "--name", "kdb", "--purpose", "x", "--repo", str(kdb))[0] == 0
    (kdb / "plugins" / "k").mkdir(parents=True)
    (kdb / "skills").rename(kdb / "plugins" / "k" / "skills")
    (kdb / "skills").symlink_to("plugins/k/skills")
    (kdb / "skills" / "kdb-build").mkdir()
    (kdb / "skills" / "kdb-build" / "SKILL.md").write_text("custom")
    assert run("install", "--repo", str(kdb), "--target", str(cfg), home=d)[0] == 0

    code, out = run("upgrade", "--all", home=d)  # dry run
    assert code == 0 and f"Lobe 'kdb' (in place) at {kdb.resolve()}\nUp to date" in out, out
    assert "update  skills/own-ask/SKILL.md" in out and f"link    {cfg}/skills/own-lint" in out, out
    assert not (cfg / "skills" / "own-lint").exists() and ask.read_text() == "# old\n"
    assert run("upgrade", "--all", "--origin", "x", home=d)[0] == 1
    code, out = run("upgrade", "--all", "--apply", home=d)
    assert code == 0 and "Upgraded 1 file(s)" in out and (cfg / "skills" / "own-lint").is_symlink(), out
    assert ask.read_text() == fresh["skills/own-ask/SKILL.md"]
    assert (own / "skills" / "own-custom" / "SKILL.md").read_text() == "mine"
    assert (kdb / "skills" / "kdb-build" / "SKILL.md").read_text() == "custom"
    commit(own)
    assert "Up to date" in run("upgrade", "--all", home=d)[1]

    # uninstall: links only, everywhere they point into this lobe; --purge only for external homes
    code, out = run("uninstall", "--repo", str(own), "--purge", "--target", str(claude))
    assert code == 1 and "external homes only" in out and (claude / "skills" / "own-ask").is_symlink(), out
    code, out = run("uninstall", "--repo", str(own), "--target", str(claude))
    assert code == 0 and "Removed 4 skill link(s)" in out, out
    assert not any((claude / "skills" / f"own-{k}").exists() for k in ["add-raw", "ingest", "ask", "lint"])
    assert (claude / "skills" / "work-wiki-ask").is_symlink() and (own / "skills" / "own-ask" / "SKILL.md").is_file()

    (home / "notes.txt").write_text("mine")
    targets = ["--target", str(claude), "--target", str(d / "other")]
    code, out = run("uninstall", "--repo", str(home), "--purge", *targets)
    assert code == 1 and "notes.txt" in out and (claude / "skills" / "work-wiki-ask").is_symlink(), out
    (home / "notes.txt").unlink()
    code, out = run("uninstall", "--repo", str(home), "--purge", *targets)
    assert code == 0 and "Removed 8 skill link(s)" in out and not home.exists(), out  # 4 skills x 2 installs
    assert (work / "llmwiki" / "old-page.md").read_text() == LEGACY
    assert subprocess.run(["git", "-C", str(work), "status", "--porcelain"], capture_output=True, text=True).stdout == ""

print("ok")
