"""Smoke tests for forge.py: create, adopt (in place and external), scan, install. Run: python3 test_forge.py"""
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

    # the header every generated skill runs resolves the repo through the install symlink
    base = claude / "skills" / "personal-ask"
    r = sh(f'SKILL_BASE_DIR="{base}"; W="$(cd -P "${{SKILL_BASE_DIR}}/../.." && pwd)/wikictl"; '
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

    # adopt external: the work repo is untouched, lobe home links to it
    home = d / "lobes" / "_adopted" / "work-wiki"
    code, out = run("create", "--name", "work-wiki", "--purpose", "Team repo.", "--repo", str(work),
                    "--wiki-dir", "llmwiki", "--raw-dir", "docs", "--home", str(home), "--target", str(claude))
    assert code == 0, out
    status = subprocess.run(["git", "-C", str(work), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert status == "", status
    assert (home / "repo").resolve() == work.resolve()
    assert "never push unless asked" in (home / "skills" / "work-wiki-ingest" / "SKILL.md").read_text()
    base = claude / "skills" / "work-wiki-lint"
    r = sh(f'SKILL_BASE_DIR="{base}"; W="$(cd -P "${{SKILL_BASE_DIR}}/../.." && pwd)/wikictl"; '
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

    # install mode works for both kinds of lobe
    for lobe, skill in [(repo, "personal-ingest"), (home, "work-wiki-ask")]:
        code, out = run("install", "--repo", str(lobe), "--target", str(d / "other"))
        assert code == 0 and (d / "other" / "skills" / skill).is_symlink(), out

print("ok")
