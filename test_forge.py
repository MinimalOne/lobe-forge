"""Smoke test: create a lobe, install it, re-run safely, refuse conflicts. Run: python3 test_forge.py"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

FORGE = Path(__file__).parent / "scripts" / "forge.py"


def run(*args, cwd=None):
    r = subprocess.run([sys.executable, str(FORGE), *args], capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    repo, home = d / "lobes" / "personal", d / "claude"
    code, out = run("create", "--name", "personal", "--purpose", "My life: notes, plans.",
                    "--repo", str(repo), "--target", str(home))
    assert code == 0, out
    for f in ["raw/.gitkeep", "wiki/index.md", "wiki/log.md", "wiki_tool.py", "LOBE.md", ".gitignore"]:
        assert (repo / f).exists(), f
    for k in ["add-raw", "ingest", "ask", "lint"]:
        skill = (repo / "skills" / f"personal-{k}" / "SKILL.md").read_text()
        assert f"name: personal-{k}\n" in skill and "{{" not in skill, k
        link = home / "skills" / f"personal-{k}"
        assert link.is_symlink() and link.resolve() == (repo / "skills" / f"personal-{k}").resolve()

    # the path line every generated skill uses resolves the repo through the symlink
    root = subprocess.run(["bash", "-c", f'cd -P "{home}/skills/personal-ask/../.." && pwd'],
                          capture_output=True, text=True).stdout.strip()
    assert Path(root) == repo.resolve(), root

    # bundled wiki_tool works on the fresh scaffold
    tool = [sys.executable, str(repo / "wiki_tool.py")]
    assert subprocess.run(tool + ["sections"], capture_output=True, text=True).stdout.strip() == "General"
    assert subprocess.run(tool + ["lint", "--strict"]).returncode == 0

    # idempotent, keeps edits
    (repo / "LOBE.md").write_text("edited")
    code, out = run("create", "--name", "personal", "--purpose", "x", "--repo", str(repo), "--target", str(home))
    assert code == 0 and "Nothing new" in out and (repo / "LOBE.md").read_text() == "edited", out

    # refuses: second lobe in same repo, bad name, clobbering a foreign skill
    assert run("create", "--name", "family", "--purpose", "x", "--repo", str(repo))[0] == 1
    assert run("create", "--name", "Bad Name", "--purpose", "x", "--repo", str(d / "y"))[0] == 1
    (home / "skills" / "work-ask").mkdir(parents=True)
    code, out = run("create", "--name", "work", "--purpose", "x", "--repo", str(d / "work"), "--target", str(home))
    assert code == 1 and "CONFLICT" in out and not (home / "skills" / "work-ask").is_symlink(), out

    # install mode on an existing lobe
    code, out = run("install", "--repo", str(repo), "--target", str(d / "other"))
    assert code == 0 and (d / "other" / "skills" / "personal-ingest").is_symlink(), out

print("ok")
