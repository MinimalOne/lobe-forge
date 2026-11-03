#!/usr/bin/env python3
"""
lobe-server — Self-hosted REST API & MCP server for lobe-forge wikis.

Part 1: Core lobe management, wiki_tool.py execution, and local-first Git with optional remote push.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import functools
import hashlib
import hmac
import html
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

ROOT = Path(__file__).resolve().parent.parent
FORGE_SCRIPT = ROOT / "scripts" / "forge.py"
PLUGIN_JSON = ROOT / ".claude-plugin" / "plugin.json"
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # page slugs; lobe names use forge.NAME_RE
# ThreadingHTTPServer runs requests in parallel; one lock keeps page check-then-write and git commits atomic.
_WRITE_LOCK = threading.RLock()


def serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper

if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "templates") not in sys.path:
    sys.path.insert(0, str(ROOT / "templates"))
import forge  # noqa: E402
from wiki_tool import (  # noqa: E402
    body_start,
    code_line_indices,
    OKF_VERSION,
    export_okf_bundle,
    human_actor,
    page_hash,
    parse_frontmatter,
    set_verified_field,
    strip_and_splice_trust_fields,
    strip_trust_fields,
)


def server_version() -> str:
    try:
        return json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"


def lobes_root() -> Path:
    path = Path(os.environ.get("LOBES_DIR", "/data/lobes")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_lobe(lobe: str) -> Path:
    if not forge.NAME_RE.match(lobe):
        raise ValueError(f"Invalid lobe name: {lobe!r} (expected lowercase letters, digits, hyphens; starts with a letter; max 30)")
    lobe_dir = (lobes_root() / lobe).resolve()
    if lobe_dir.parent != lobes_root() or not (lobe_dir / "wikictl").exists():
        raise FileNotFoundError(f"Lobe {lobe!r} not found in {lobes_root()}")
    return lobe_dir


def git_auth_args_and_env(remote_url: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Build non-interactive Git args and env, injecting LOBE_GIT_TOKEN for HTTP(S) remotes if set."""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        # push and pull run under _WRITE_LOCK: a stalled connection must fail, not block every write
        "GIT_SSH_COMMAND": os.environ.get(
            "GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"),
        "GIT_HTTP_LOW_SPEED_LIMIT": os.environ.get("GIT_HTTP_LOW_SPEED_LIMIT", "1"),
        "GIT_HTTP_LOW_SPEED_TIME": os.environ.get("GIT_HTTP_LOW_SPEED_TIME", "30"),
    }
    extra_args: list[str] = []
    token = os.environ.get("LOBE_GIT_TOKEN", "").strip()
    url = urlparse(remote_url or "")
    # Only the configured host gets the token: a caller-chosen remote_url must not receive it.
    # LOBE_GIT_TOKEN_HOST may carry a scheme, port or path; a port, when given, must match too.
    want = urlparse("//" + os.environ.get("LOBE_GIT_TOKEN_HOST", "github.com").strip().split("://")[-1])
    if (token and url.scheme in ("http", "https") and url.hostname and url.hostname == want.hostname
            and (want.port is None or url.port == want.port)):
        if ":" in token:
            creds = token
        elif token.startswith("glpat-") or (remote_url and "gitlab" in remote_url.lower()):
            creds = f"oauth2:{token}"
        elif remote_url and "bitbucket" in remote_url.lower():
            creds = f"x-token-auth:{token}"
        else:
            creds = f"x-access-token:{token}"
        basic = base64.b64encode(creds.encode("utf-8")).decode("ascii")
        # URL-scoped key: git sends the header only to requests for this host
        scope = f"{url.scheme}://{url.netloc.rpartition('@')[2]}/"
        extra_args.extend(["-c", f"http.{scope}.extraHeader=Authorization: Basic {basic}"])
    return extra_args, env


# Only these talk to a remote; every other git command runs without the token on its command line.
NETWORK_GIT = {"push", "pull", "fetch", "ls-remote", "clone"}


class GitError(Exception):
    """A failed git command, carrying git's own message and never the command line."""


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r_url = None
    if args and args[0] in NETWORK_GIT:
        r_url = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip() or None
    auth_args, env = git_auth_args_and_env(r_url)
    r = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=lobe-server",
            "-c",
            "user.email=lobe-server@local",
            *auth_args,
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and r.returncode != 0:
        # not CalledProcessError: its message is the full command line, auth header included
        raise GitError(f"git {args[0] if args else ''} failed: {(r.stderr or r.stdout).strip() or f'exit {r.returncode}'}")
    return r


def verify_remote_access(remote_url: str) -> bool:
    """
    Verify read/access to `remote_url` via `git ls-remote --heads`.
    Raises ValueError if unreachable or unauthorized. Returns True if remote already has commits/branches.
    """
    auth_args, env = git_auth_args_and_env(remote_url)
    r = subprocess.run(
        ["git", *auth_args, "ls-remote", "--heads", remote_url],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip() or "authentication failed or repository not found"
        raise ValueError(f"Cannot access remote repository {remote_url!r}: {msg}")
    return bool(r.stdout.strip())


def l_paths(lobe: str) -> dict[str, Path]:
    """Resolve LOBE, REPO, WIKI, and RAW paths via wikictl paths."""
    lobe_dir = resolve_lobe(lobe)
    r = subprocess.run([str(lobe_dir / "wikictl"), "paths"], capture_output=True, text=True, check=True)
    out: dict[str, Path] = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = Path(v.strip().strip("'\""))
    return out


def has_remote(repo: Path, remote: str = "origin") -> str | None:
    r = git(repo, "remote", "get-url", remote, check=False)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def autopush_enabled(repo: Path) -> bool:
    """The lobe's own `lobe.autopush` when set (true or false); otherwise the LOBE_GIT_PUSH default."""
    r = git(repo, "config", "--bool", "--get", "lobe.autopush", check=False)  # yes/on/1 -> true; unset or invalid: exit != 0
    if r.returncode == 0:
        return r.stdout.strip() == "true"
    return os.environ.get("LOBE_GIT_PUSH", "").lower() in ("1", "true", "yes")


def git_commit_and_optional_push(repo: Path, message: str, paths: list[str] | None = None) -> dict[str, Any]:
    """
    Commit changes locally in `repo` (mandatory) and push to `origin` only if:
      1. A remote `origin` URL is configured on the repo, AND
      2. Push is enabled for the lobe (see autopush_enabled).
    """
    if paths:
        git(repo, "add", "--", *paths)
    else:
        git(repo, "add", "-A")

    committed = False
    commit_hash = None
    # staged changes only: untracked or unstaged files elsewhere would make `git commit` fail
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
        git(repo, "commit", "-q", "-m", message)
        committed = True
        commit_hash = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    else:
        head = git(repo, "rev-parse", "--short", "HEAD", check=False)
        commit_hash = head.stdout.strip() if head.returncode == 0 else None

    remote_url = has_remote(repo)
    should_push = bool(remote_url and autopush_enabled(repo))

    pushed = False
    push_error = None
    if should_push:
        branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
        pr = git(repo, "push", "-u", "origin", branch, check=False)
        if pr.returncode == 0:
            pushed = True
        else:
            push_error = (pr.stderr or pr.stdout).strip()

    return {
        "committed": committed,
        "commit": commit_hash,
        "remote": remote_url,
        "pushed": pushed,
        "push_error": push_error,
    }


def list_lobes() -> list[dict[str, Any]]:
    root = lobes_root()
    items: list[dict[str, Any]] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "wikictl").exists():
            continue
        repo = l_paths(d.name)["REPO"]
        settings = forge.lobe_settings(d)
        purpose = "" if isinstance(settings, str) else settings[1]
        remote = has_remote(repo)
        items.append({
            "name": d.name,
            "purpose": purpose,
            "repo": str(repo),
            "remote": remote,
            "autopush": bool(remote and autopush_enabled(repo)),
        })
    return items


def generate_lobe_skills(lobe: str, mode: str = "mcp") -> dict[str, Any]:
    """Render the four <name>-{add-raw,ingest,ask,lint} skills for an existing lobe on lobe-server."""
    settings = forge.lobe_settings(resolve_lobe(lobe))
    if isinstance(settings, str):
        raise FileNotFoundError(settings)
    _, purpose, wiki_rel, raw_rel, _, origin = settings
    skills = forge.render_skills(
        name=lobe,
        purpose=purpose,
        wiki_dir=wiki_rel,
        raw_dir=raw_rel,
        mode=mode,
        external=False,
        origin=origin,
    )
    skill_names = [f"{lobe}-{k}" for k in forge.KINDS]
    return {
        "lobe": lobe,
        "purpose": purpose,
        "mode": mode,
        "skill_names": skill_names,
        "skills": skills,
        "install_instructions": (
            "Write each '<skill_name>/SKILL.md' entry in 'skills' into the user's selected local AI "
            "skill directories (for example: ~/.claude/skills/<skill_name>/SKILL.md, "
            "~/.claude-personal/skills/<skill_name>/SKILL.md, "
            "~/.gemini/config/skills/<skill_name>/SKILL.md, or "
            "~/.gemini-personal/.gemini/config/skills/<skill_name>/SKILL.md)."
        ),
    }


@serialized
def create_lobe(
    name: str,
    purpose: str,
    remote_url: str | None = None,
    autopush: bool | None = None,
    generate_skills: bool = False,
    skill_mode: str = "mcp",
) -> dict[str, Any]:
    if not forge.NAME_RE.match(name):  # before any clone: a rejected name must leave nothing on disk
        raise ValueError(f"Invalid lobe name: {name!r} (expected lowercase letters, digits, hyphens; starts with a letter; max 30)")
    repo_dir = lobes_root() / name

    cloned = False
    if remote_url:
        remote_has_commits = verify_remote_access(remote_url)
        if remote_has_commits and not repo_dir.exists():
            auth_args, env = git_auth_args_and_env(remote_url)
            cr = subprocess.run(
                ["git", *auth_args, "clone", "-q", remote_url, str(repo_dir)],
                capture_output=True,
                text=True,
                env=env,
            )
            if cr.returncode != 0:
                raise RuntimeError((cr.stderr or cr.stdout).strip())
            cloned = True

    forge_cmd = [sys.executable, str(FORGE_SCRIPT), "create", "--name", name, "--purpose", purpose, "--repo", str(repo_dir)]
    if cloned and (repo_dir / "llmwiki").is_dir() and not (repo_dir / "wiki").is_dir():
        forge_cmd.extend(["--wiki-dir", "llmwiki"])
        if (repo_dir / "docs").is_dir() and not (repo_dir / "raw").is_dir():
            forge_cmd.extend(["--raw-dir", "docs"])

    r = subprocess.run(forge_cmd, capture_output=True, text=True)
    if r.returncode != 0 and not (cloned and (repo_dir / "wikictl").exists()):
        raise RuntimeError((r.stderr or r.stdout).strip())

    if remote_url:
        configure_remote(name, remote_url=remote_url, autopush=autopush, verify=False)

    git_info = git_commit_and_optional_push(repo_dir, f"lobe: initialize {name}")
    out: dict[str, Any] = {"name": name, "purpose": purpose, "repo": str(repo_dir), "cloned": cloned, **git_info}
    if generate_skills:
        skill_data = generate_lobe_skills(name, mode=skill_mode)
        out["skill_mode"] = skill_data["mode"]
        out["skill_names"] = skill_data["skill_names"]
        out["skills"] = skill_data["skills"]
        out["install_instructions"] = skill_data["install_instructions"]
    return out


@serialized
def configure_remote(
    lobe: str,
    remote_url: str | None,
    autopush: bool | None = True,
    verify: bool = True,
) -> dict[str, Any]:
    """autopush None leaves lobe.autopush unset, so the LOBE_GIT_PUSH default decides."""
    repo = l_paths(lobe)["REPO"]
    if remote_url and verify:
        verify_remote_access(remote_url)
    existing = has_remote(repo)
    if remote_url:
        if existing:
            git(repo, "remote", "set-url", "origin", remote_url)
        else:
            git(repo, "remote", "add", "origin", remote_url)
    elif existing:
        git(repo, "remote", "remove", "origin")

    if autopush is None:
        git(repo, "config", "--unset", "lobe.autopush", check=False)  # exit 5 when already unset
    else:
        git(repo, "config", "lobe.autopush", "true" if (remote_url and autopush) else "false")
    remote = has_remote(repo)
    return {"lobe": lobe, "remote": remote, "autopush": bool(remote and autopush_enabled(repo))}


@serialized
def sync_lobe(lobe: str, push: bool = True) -> dict[str, Any]:
    """Pull latest changes from origin (with safe rebase abort on conflict) and optionally push."""
    repo = l_paths(lobe)["REPO"]
    remote_url = has_remote(repo)
    if not remote_url:
        raise ValueError(f"Lobe {lobe!r} has no remote repository configured")

    remote_has_commits = verify_remote_access(remote_url)
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"

    pulled = False
    if remote_has_commits:
        ls_branch = git(repo, "ls-remote", "--heads", "origin", branch, check=False)
        if ls_branch.returncode == 0 and ls_branch.stdout.strip():
            pr = git(repo, "pull", "--rebase", "origin", branch, check=False)
            if pr.returncode != 0:
                git(repo, "rebase", "--abort", check=False)
                raise RuntimeError(f"Git pull --rebase conflict/error (rebase aborted): {(pr.stderr or pr.stdout).strip()}")
            pulled = True

    pushed = False
    if push:
        push_r = git(repo, "push", "-u", "origin", branch, check=False)
        if push_r.returncode != 0:
            raise RuntimeError(f"Git push failed: {(push_r.stderr or push_r.stdout).strip()}")
        pushed = True

    head = git(repo, "rev-parse", "--short", "HEAD", check=False)
    commit_hash = head.stdout.strip() if head.returncode == 0 else None
    return {
        "lobe": lobe,
        "remote": remote_url,
        "pulled": pulled,
        "pushed": pushed,
        "commit": commit_hash,
    }


def run_wiki_tool(lobe: str, *args: str) -> dict[str, Any]:
    lobe_dir = resolve_lobe(lobe)
    r = subprocess.run([str(lobe_dir / "wikictl"), *args], capture_output=True, text=True)
    return {
        "returncode": r.returncode,
        "stdout": r.stdout,
        "stderr": r.stderr,
    }


@serialized
def add_raw(lobe: str, filename: str, content: str) -> dict[str, Any]:
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"Invalid raw filename: {filename!r}")
    paths = l_paths(lobe)
    raw_file = paths["RAW"] / safe_name
    rel = str(raw_file.relative_to(paths["REPO"]))
    if raw_file.exists():  # raw/ is append-only: pages cite a source by path, so its content must not change
        raise RuntimeError(f"{rel} already exists; save the source under another name")
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(content, encoding="utf-8")
    git_info = git_commit_and_optional_push(paths["REPO"], f"raw: add {safe_name}", [rel])
    return {"lobe": lobe, "path": rel, **git_info}


def read_raw(lobe: str, filename: str) -> dict[str, Any]:
    """Read a raw source by name, by path inside the raw folder, or by the repo path lobe_pending prints."""
    paths = l_paths(lobe)
    raw_root = paths["RAW"].resolve()
    rel = Path(filename)
    prefix = paths["RAW"].relative_to(paths["REPO"]).parts  # ('raw',) or ('docs',)
    if rel.parts[: len(prefix)] == prefix:
        rel = Path(*rel.parts[len(prefix):])
    raw_file = (raw_root / rel).resolve()
    if raw_root not in raw_file.parents:  # rejects "", "..", absolute paths and links out of the raw folder
        raise ValueError(f"Invalid raw filename: {filename!r} (must be inside {paths['RAW'].name}/)")
    if not raw_file.is_file():
        raise FileNotFoundError(f"Raw source {filename!r} not found in lobe {lobe!r}")
    return {
        "lobe": lobe,
        "filename": raw_file.relative_to(raw_root).as_posix(),
        "path": str(raw_file.relative_to(paths["REPO"])),
        "content": raw_file.read_text(encoding="utf-8", errors="replace"),
    }


def page_name(slug: str) -> str:
    """`wiki/k8s-1.29.md` -> `k8s-1.29`: not Path.stem, which would cut at the last dot and open another page."""
    return Path(slug).name.removesuffix(".md")


def page_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def h2_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    """(heading text, heading index, end index exclusive) for each `## ` heading outside frontmatter and code blocks."""
    code_lines = code_line_indices(lines)
    heads = [i for i in range(body_start(lines), len(lines)) if i not in code_lines and lines[i].startswith("## ")]
    return [(lines[h][3:].strip(), h, e) for h, e in zip(heads, heads[1:] + [len(lines)])]


def section_span(lines: list[str], section: str) -> tuple[int, int]:
    want = section.strip().removeprefix("## ").strip().lower()
    spans = h2_spans(lines)
    for name, s, e in spans:
        if name.lower() == want:
            return s, e
    names = ", ".join(n for n, _, _ in spans) or "(no ## sections)"
    raise ValueError(f"Section {section!r} not found; page has: {names}")


def check_sha(text: str | None, expected_sha: str | None) -> None:
    if expected_sha is None:
        return
    current = page_sha(text) if text is not None else None
    if current != expected_sha:
        raise RuntimeError(
            f"Page changed since it was read (expected_sha {expected_sha}, current {current or 'none: page does not exist'}). "
            "Read it again and reapply the change."
        )


def read_page(
    lobe: str,
    slug: str,
    section: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    clean_slug = page_name(slug)
    paths = l_paths(lobe)
    page_file = paths["WIKI"] / f"{clean_slug}.md"
    if not page_file.exists():
        raise FileNotFoundError(f"Page {clean_slug!r} not found in lobe {lobe!r}")
    text = page_file.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: dict[str, Any] = {
        "lobe": lobe,
        "slug": clean_slug,
        "path": str(page_file.relative_to(paths["REPO"])),
        "sha": page_sha(text),
        "lines": len(lines),
    }
    if section:
        s, e = section_span(lines, section)
        start_line, end_line = s + 1, e
    if start_line is None and end_line is None:
        out["content"] = text
        return out
    start = 1 if start_line is None else int(start_line)
    end = len(lines) if end_line is None else min(len(lines), int(end_line))
    if start < 1 or start > end:
        raise ValueError(f"Invalid line range {start_line}-{end_line} (page has {len(lines)} lines)")
    # a partial read hides the page's headings, so list them for follow-up section reads
    out.update(start_line=start, end_line=end, sections=[n for n, _, _ in h2_spans(lines)])
    out["content"] = "".join(lines[start - 1 : end])
    return out


@serialized
def write_page(
    lobe: str,
    slug: str,
    content: str,
    section: str | None = None,
    description: str | None = None,
    log_entry: str | None = None,
    expected_sha: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    clean_slug = page_name(slug)
    if not SLUG_RE.match(clean_slug):
        raise ValueError(f"Invalid page slug: {slug!r} (expected lowercase letters, digits, hyphens)")
    paths = l_paths(lobe)
    page_file = paths["WIKI"] / f"{clean_slug}.md"
    is_new = not page_file.exists()
    disk_text = None if is_new else page_file.read_text(encoding="utf-8")
    check_sha(disk_text, expected_sha)

    final_content = strip_and_splice_trust_fields(
        content,
        existing_disk_text=disk_text,
        actor=(actor or "").strip() or "mcp-client",
    )
    if disk_text is not None and strip_trust_fields(content) == strip_trust_fields(disk_text):
        final_content = disk_text  # unchanged page: keep the old stamp so the save is a no-op

    staged = [str(page_file.relative_to(paths["REPO"]))]
    if section and description:
        # before the page write: index-add fails without writing, so a bad section leaves nothing on disk
        idx = run_wiki_tool(lobe, "index-add", "--section", section, "--page", clean_slug, "--description", description)
        if idx["returncode"] != 0:  # unknown section: a bad request (400), not a conflict
            raise ValueError((idx["stderr"] or idx["stdout"]).strip())
        staged.append(str((paths["WIKI"] / "index.md").relative_to(paths["REPO"])))
    page_file.write_text(final_content, encoding="utf-8")

    if log_entry and log_entry.strip():
        log_file = paths["WIKI"] / "log.md"
        existing_log = log_file.read_text(encoding="utf-8") if log_file.exists() else f"# {lobe.title()} Wiki — Log\n"
        sep = "\n" if existing_log.endswith("\n") else "\n\n"
        log_file.write_text(existing_log + sep + log_entry.strip() + "\n", encoding="utf-8")
        staged.append(str(log_file.relative_to(paths["REPO"])))
        # after the append, like the local ingest skill; housekeeping only: older tooling lacks rotate-log
        if run_wiki_tool(lobe, "rotate-log")["returncode"] == 0 and (paths["WIKI"] / "log").is_dir():
            staged.append(str((paths["WIKI"] / "log").relative_to(paths["REPO"])))

    action = "create" if is_new else "update"
    git_info = git_commit_and_optional_push(paths["REPO"], f"wiki: {action} {clean_slug}", staged)
    return {"lobe": lobe, "slug": clean_slug, "created": is_new, "sha": page_sha(final_content), **git_info}


def apply_edit(text: str, edit: dict[str, Any]) -> str:
    if not isinstance(edit, dict):
        raise ValueError(f"Each edit must be an object, got {edit!r}")
    op, content = edit.get("op"), edit.get("content")
    if not isinstance(content, str):
        raise ValueError("Each edit needs a string 'content'")
    if op == "find_replace":
        find = edit.get("find")
        if not isinstance(find, str) or not find:
            raise ValueError("find_replace needs a non-empty string 'find'")
        expected = edit.get("expected_count")
        expected = 1 if expected is None else expected
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ValueError(f"expected_count must be an integer >= 1, got {expected!r}")
        found = text.count(find)
        if found != expected:
            raise ValueError(f"'find' text matched {found} time(s), expected {expected}: {find[:80]!r}")
        return text.replace(find, content)
    if op not in ("append", "replace_section"):
        raise ValueError(f"Unknown edit op {op!r} (use find_replace, append, or replace_section)")
    block = content if content.endswith("\n") else content + "\n"
    section = edit.get("section")
    if op == "append" and not section:
        return (text if text.endswith("\n") or not text else text + "\n") + block
    if not section:
        raise ValueError("replace_section needs 'section'")
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    s, e = section_span(lines, section)
    if op == "append":
        kept = lines[s:e]
        while len(kept) > 1 and not kept[-1].strip():
            kept.pop()
    else:
        kept = [lines[s], "\n"]  # keep the heading, replace everything under it
    gap = ["\n"] if e < len(lines) else []
    return "".join(lines[:s] + kept + [block] + gap + lines[e:])


@serialized
def edit_page(
    lobe: str,
    slug: str,
    edits: list[dict[str, Any]],
    expected_sha: str | None = None,
    log_entry: str | None = None,
    index_section: str | None = None,
    index_description: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Apply edits in order to an existing page and commit once; any failing edit aborts all of them."""
    if not isinstance(edits, list) or not edits:
        raise ValueError("edits must be a non-empty list")
    page = read_page(lobe, slug)
    check_sha(page["content"], expected_sha)
    text = page["content"]
    for edit in edits:
        text = apply_edit(text, edit)
    return write_page(lobe, page["slug"], text, index_section, index_description, log_entry, expected_sha=page["sha"], actor=actor)


@serialized
def verify_page(
    lobe: str,
    slug: str,
    expected_hash: str,
    by: str | None = None,
) -> dict[str, Any]:
    """Human verification of a wiki page pinned by its page_hash (content without trust fields).
    Only reachable through the human-gated REST route; never exposed as an MCP tool."""
    clean_slug = page_name(slug)
    if not SLUG_RE.match(clean_slug):
        raise ValueError(f"Invalid page slug: {slug!r} (expected lowercase letters, digits, hyphens)")
    paths = l_paths(lobe)
    page_file = paths["WIKI"] / f"{clean_slug}.md"
    if not page_file.is_file():
        raise FileNotFoundError(f"Page '{clean_slug}' not found in lobe '{lobe}'")

    text = page_file.read_text(encoding="utf-8")
    current_hash = page_hash(text)
    if expected_hash != current_hash:
        raise RuntimeError(f"Page content hash {current_hash} does not match expected_hash {expected_hash}")

    by_val = human_actor(by)
    at_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    final_content = set_verified_field(text, by=by_val, at=at_iso, sha=current_hash)
    page_file.write_text(final_content, encoding="utf-8")

    staged = [str(page_file.relative_to(paths["REPO"]))]
    git_info = git_commit_and_optional_push(paths["REPO"], f"wiki: verify {clean_slug}", staged)
    return {
        "lobe": lobe,
        "slug": clean_slug,
        "sha": page_sha(final_content),
        "verified": {"by": by_val, "at": at_iso, "sha": current_hash},
        **git_info,
    }


def export_okf(lobe: str) -> dict[str, Any]:
    """Whole-wiki export for REST callers. Not an MCP tool: one call would fill an agent's context."""
    paths = l_paths(lobe)
    exported = export_okf_bundle(paths["WIKI"])
    return {
        "lobe": lobe,
        "okf_version": OKF_VERSION,
        "page_count": len(exported),
        "files": exported,
    }


# ── Part 2: MCP Tool Definitions & JSON-RPC 2.0 Dispatcher ──────────────────

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "lobe_list",
        "description": "List all lobes hosted on lobe-server and their Git remote status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "lobe_create",
        "description": (
            "Create a new git-backed lobe wiki (or clone an existing remote_url). "
            "Ask the user whether they also want to generate and install the four lobe-named skills "
            "(<name>-add-raw, <name>-ingest, <name>-ask, <name>-lint) on their client machine "
            "(pass generate_skills=true)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Lobe name: lowercase letters, digits and hyphens, starting with a letter, at most 30 characters (e.g. hack, personal)."},
                "purpose": {"type": "string", "description": "One-line purpose of the lobe."},
                "remote_url": {"type": "string", "description": "Optional remote Git URL (origin)."},
                "autopush": {"type": ["boolean", "null"], "description": "Auto-push commits to origin. Omit or null to follow the server default (LOBE_GIT_PUSH)."},
                "generate_skills": {
                    "type": "boolean",
                    "description": "If true, also return the four rendered <name>-* SKILL.md files to install on the client machine.",
                },
                "skill_mode": {
                    "type": "string",
                    "enum": ["mcp", "local"],
                    "description": "Skill template mode when generate_skills=true: 'mcp' (default, calls lobe-server MCP tools) or 'local' (calls local wikictl).",
                },
            },
            "required": ["name", "purpose"],
        },
    },
    {
        "name": "lobe_generate_skills",
        "description": (
            "Generate the four lobe-named skills (<name>-add-raw, <name>-ingest, <name>-ask, <name>-lint) "
            "for any existing lobe on lobe-server so the client can install them into local skill directories "
            "(e.g. ~/.claude/skills/ or ~/.gemini/config/skills/). Defaults to mode='mcp' (remote MCP skills); "
            "pass mode='local' for local wikictl skills."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string", "description": "Name of the existing lobe on lobe-server."},
                "mode": {
                    "type": "string",
                    "enum": ["mcp", "local"],
                    "description": "'mcp' (default, calls lobe-server MCP tools without a local clone) or 'local' (calls local wikictl).",
                },
            },
            "required": ["lobe"],
        },
    },
    {
        "name": "lobe_configure_remote",
        "description": "Configure or remove the optional remote Git URL and auto-push setting for a lobe.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "remote_url": {"type": ["string", "null"], "description": "Remote Git URL, or null/empty to remove."},
                "autopush": {"type": ["boolean", "null"], "description": "Push automatically after local commits (default true). null: follow the server default (LOBE_GIT_PUSH)."},
            },
            "required": ["lobe"],
        },
    },
    {
        "name": "lobe_sync",
        "description": "Pull latest changes from origin (git pull --rebase) and optionally push local commits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "push": {"type": "boolean", "description": "Push local commits after pulling (default: true)."},
            },
            "required": ["lobe"],
        },
    },
    {
        "name": "lobe_search",
        "description": "Search a lobe wiki using BM25F field-weighted ranking and recency decay.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "terms": {"type": "array", "items": {"type": "string"}, "description": "Search terms / regex alternations."},
                "no_decay": {"type": "boolean", "description": "Disable recency decay on last_updated."},
            },
            "required": ["lobe", "terms"],
        },
    },
    {
        "name": "lobe_links",
        "description": "Show outgoing and incoming wiki links for a page.",
        "inputSchema": {
            "type": "object",
            "properties": {"lobe": {"type": "string"}, "page": {"type": "string"}},
            "required": ["lobe", "page"],
        },
    },
    {
        "name": "lobe_sections",
        "description": "List ## sections in the lobe's wiki/index.md.",
        "inputSchema": {
            "type": "object",
            "properties": {"lobe": {"type": "string"}},
            "required": ["lobe"],
        },
    },
    {
        "name": "lobe_pending",
        "description": "List raw sources in raw/ that no wiki page cites yet.",
        "inputSchema": {
            "type": "object",
            "properties": {"lobe": {"type": "string"}},
            "required": ["lobe"],
        },
    },
    {
        "name": "lobe_lint",
        "description": "Audit a lobe wiki for frontmatter, broken links, orphans, index drift, and missing sources.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "strict": {"type": "boolean", "description": "Exit non-zero only on hard errors."},
            },
            "required": ["lobe"],
        },
    },
    {
        "name": "lobe_add_raw",
        "description": "Save a new source file into the lobe's raw/ folder and commit locally (with optional remote push).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "filename": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["lobe", "filename", "content"],
        },
    },
    {
        "name": "lobe_read_raw",
        "description": "Read a raw source file from the lobe's raw/ folder on lobe-server.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "filename": {"type": "string", "description": "Filename in the raw folder (e.g. 2026-10-30-notes.md), or a path as lobe_pending prints it (e.g. docs/sub/notes.md)."},
            },
            "required": ["lobe", "filename"],
        },
    },
    {
        "name": "lobe_read_page",
        "description": (
            "Read a markdown page from the lobe's wiki/ folder, or only one `## ` section or a line range "
            "(use the [L<start>-<end>] range from lobe_search). Returns `sha` for expected_sha on later writes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "slug": {"type": "string"},
                "section": {"type": "string", "description": "Return only this `## ` section (heading text, case-insensitive). Partial reads also return the page's `sections`."},
                "start_line": {"type": "integer", "description": "First line to return (1-based, counts frontmatter)."},
                "end_line": {"type": "integer", "description": "Last line to return (inclusive)."},
            },
            "required": ["lobe", "slug"],
        },
    },
    {
        "name": "lobe_write_page",
        "description": "Create or update a wiki page, optionally add to index.md and append to log.md, and commit locally (with optional push).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "slug": {"type": "string"},
                "content": {"type": "string"},
                "section": {"type": "string", "description": "Optional index.md section for new pages."},
                "description": {"type": "string", "description": "Optional <=25-word index description."},
                "log_entry": {"type": "string", "description": "Optional markdown block to append to wiki/log.md in the same commit."},
                "expected_sha": {"type": "string", "description": "sha from lobe_read_page; the write fails if the page changed since."},
                "actor": {"type": "string", "description": "Optional actor identity for generated.by (defaults to 'mcp-client')."},
            },
            "required": ["lobe", "slug", "content"],
        },
    },
    {
        "name": "lobe_edit_page",
        "description": (
            "Change part of an existing wiki page without resending all of it. Applies `edits` in order, "
            "all or nothing, and commits once. Ops: find_replace (exact text, must match expected_count times), "
            "append (to the end of the page, or of `section`), replace_section (replace the body under a `## ` heading)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "slug": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {"type": "string", "enum": ["find_replace", "append", "replace_section"]},
                            "content": {"type": "string", "description": "Replacement or appended text."},
                            "find": {"type": "string", "description": "find_replace: exact text to replace."},
                            "expected_count": {"type": "integer", "description": "find_replace: required number of matches (default 1)."},
                            "section": {"type": "string", "description": "append/replace_section: `## ` heading text."},
                        },
                        "required": ["op", "content"],
                    },
                },
                "expected_sha": {"type": "string", "description": "sha from lobe_read_page; the edit fails if the page changed since."},
                "log_entry": {"type": "string", "description": "Optional markdown block to append to wiki/log.md in the same commit."},
                "index_section": {"type": "string", "description": "With index_description: add the page to this index.md section."},
                "index_description": {"type": "string", "description": "Optional <=25-word index description."},
                "actor": {"type": "string", "description": "Optional actor identity for generated.by (defaults to 'mcp-client')."},
            },
            "required": ["lobe", "slug", "edits"],
        },
    },
]


def call_mcp_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "lobe_list":
        return {"lobes": list_lobes()}
    if name == "lobe_create":
        return create_lobe(
            name=args["name"],
            purpose=args["purpose"],
            remote_url=args.get("remote_url"),
            autopush=None if args.get("autopush") is None else bool(args["autopush"]),
            generate_skills=bool(args.get("generate_skills", False)),
            skill_mode=str(args.get("skill_mode", "mcp")),
        )
    if name == "lobe_generate_skills":
        return generate_lobe_skills(
            lobe=args["lobe"],
            mode=str(args.get("mode", "mcp")),
        )
    if name == "lobe_configure_remote":
        return configure_remote(
            lobe=args["lobe"],
            remote_url=args.get("remote_url"),
            autopush=None if (v := args.get("autopush", True)) is None else bool(v),
        )
    if name == "lobe_sync":
        return sync_lobe(
            lobe=args["lobe"],
            push=bool(args.get("push", True)),
        )
    if name == "lobe_search":
        terms = [t for t in args.get("terms", []) if t]
        if not terms:
            raise ValueError("terms must be a non-empty list of search strings")
        cmd = ["search", *(["--no-decay"] if args.get("no_decay") else []), "--", *terms]
        return run_wiki_tool(args["lobe"], *cmd)
    if name == "lobe_links":
        return run_wiki_tool(args["lobe"], "links", "--", args["page"])
    if name == "lobe_sections":
        return run_wiki_tool(args["lobe"], "sections")
    if name == "lobe_pending":
        return run_wiki_tool(args["lobe"], "pending")
    if name == "lobe_lint":
        cmd = ["lint", *(["--strict"] if args.get("strict") else []), *(["--all"] if args.get("all") else [])]
        return run_wiki_tool(args["lobe"], *cmd)
    if name == "lobe_add_raw":
        return add_raw(args["lobe"], args["filename"], args["content"])
    if name == "lobe_read_raw":
        return read_raw(args["lobe"], args["filename"])
    if name == "lobe_read_page":
        return read_page(args["lobe"], args["slug"], args.get("section"), args.get("start_line"), args.get("end_line"))
    if name == "lobe_write_page":
        return write_page(
            lobe=args["lobe"],
            slug=args["slug"],
            content=args["content"],
            section=args.get("section"),
            description=args.get("description"),
            log_entry=args.get("log_entry"),
            expected_sha=args.get("expected_sha"),
            actor=args.get("actor"),
        )
    if name == "lobe_edit_page":
        return edit_page(
            args["lobe"], args["slug"], args["edits"], args.get("expected_sha"), args.get("log_entry"),
            args.get("index_section"), args.get("index_description"), actor=args.get("actor"),
        )
    raise ValueError(f"Unknown MCP tool: {name!r}")


# Oldest first. Streamable HTTP needs 2025-03-26+; 2025-06-18 drops JSON-RPC batching, which we already reject.
MCP_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")


def handle_mcp_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 MCP message and return a response dict (or None for notifications)."""
    if not isinstance(msg, dict):  # e.g. a JSON-RPC batch: one bad message must not crash the stdio loop or 500 over HTTP
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request: expected one JSON-RPC object"}}
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if "id" not in msg:
        return None  # JSON-RPC notification (e.g. notifications/initialized)

    if method == "initialize":
        # answer with the client's version when we support it, else our newest (MCP version negotiation)
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": requested if requested in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[-1],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lobe-server", "version": server_version()},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": MCP_TOOLS}}
    if method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments") or {}
        try:
            res = call_mcp_tool(tool_name, tool_args)
            text = res["stdout"] if isinstance(res, dict) and set(res.keys()) == {"returncode", "stdout", "stderr"} and res["returncode"] == 0 else json.dumps(res, indent=2)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": res,
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ── Part 2: HTTP REST API & Streamable HTTP /mcp Handler ────────────────────

def oauth_issuer() -> str:
    """OIDC issuer of the provider (e.g. Authentik .../application/o/<slug>/). Empty disables OAuth."""
    return os.environ.get("LOBE_OAUTH_ISSUER", "").strip()


def protected_resource_metadata() -> dict[str, Any]:
    """RFC 9728 metadata telling OAuth clients (claude.ai) which authorization server to use."""
    public = os.environ.get("LOBE_PUBLIC_URL", "").strip().rstrip("/")
    return {
        "resource": f"{public}/mcp",
        "authorization_servers": [oauth_issuer()],
        "bearer_methods_supported": ["header"],
    }


@functools.lru_cache(maxsize=1)
def introspection_endpoint(issuer: str) -> str:
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())["introspection_endpoint"]


# shortcut: in-memory cache per process, emptied on restart; fine for one lobe-server container.
_TOKEN_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TOKEN_CACHE_TTL = 60.0


def introspect_token_info(token: str) -> dict[str, Any] | None:
    """Validate an OAuth access token with the issuer's RFC 7662 introspection endpoint, returning token info dict."""
    now = time.time()
    cached = _TOKEN_CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]
    client_id = os.environ.get("LOBE_OAUTH_CLIENT_ID", "").strip()
    secret = os.environ.get("LOBE_OAUTH_CLIENT_SECRET", "").strip()
    try:
        req = urllib.request.Request(
            introspection_endpoint(oauth_issuer()),
            data=urlencode({"token": token}).encode("utf-8"),
            headers={
                "Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read())
    except Exception:
        return None  # fail closed when the authorization server is unreachable
    if not info.get("active"):
        return None
    allowed_clients = {client_id}
    human_client_id = os.environ.get("LOBE_HUMAN_CLIENT_ID", "").strip()
    if human_client_id:
        allowed_clients.add(human_client_id)
    allowed_clients.discard("")
    if allowed_clients and info.get("client_id") and info["client_id"] not in allowed_clients:
        return None
    for k, v in list(_TOKEN_CACHE.items()):
        if v[0] <= now:
            _TOKEN_CACHE.pop(k, None)  # not del: another request thread may have pruned it already
    exp = min(now + _TOKEN_CACHE_TTL, float(info.get("exp", now + _TOKEN_CACHE_TTL)))
    _TOKEN_CACHE[token] = (exp, info)
    return info


def introspect_token(token: str) -> bool:
    """Validate an OAuth access token with the issuer's RFC 7662 introspection endpoint."""
    return introspect_token_info(token) is not None


def check_auth(auth_header: str | None) -> bool:
    expected = os.environ.get("LOBE_API_TOKEN", "").strip()
    human = os.environ.get("LOBE_HUMAN_TOKEN", "").strip()
    if not expected and not human and not oauth_issuer():
        return True
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    token = auth_header.removeprefix("Bearer ").strip()
    if expected and hmac.compare_digest(token.encode(), expected.encode()):
        return True
    if human and hmac.compare_digest(token.encode(), human.encode()):
        return True
    return bool(oauth_issuer()) and introspect_token(token)


def check_human_auth(auth_header: str | None) -> tuple[bool, str | None]:
    """
    Check if the caller has human verification credentials.
    Returns (is_human, reviewer_identity); identity is None for credentials that carry no name.
    - If server has no auth configured (open dev), returns (True, None).
    - If LOBE_HUMAN_TOKEN matches Bearer token, returns (True, None).
    - If OAuth token matches LOBE_HUMAN_CLIENT_ID or has LOBE_HUMAN_SCOPE (default: lobe:verify),
      returns (True, username/sub).
    - If only LOBE_API_TOKEN or a non-human OAuth token is provided, returns (False, None).
    """
    expected = os.environ.get("LOBE_API_TOKEN", "").strip()
    human = os.environ.get("LOBE_HUMAN_TOKEN", "").strip()
    issuer = oauth_issuer()
    if not expected and not human and not issuer:
        return True, None
    if not auth_header or not auth_header.startswith("Bearer "):
        return False, None
    token = auth_header.removeprefix("Bearer ").strip()
    if human and hmac.compare_digest(token.encode(), human.encode()):
        return True, None
    if issuer:
        info = introspect_token_info(token)
        if not info:
            return False, None
        human_client_id = os.environ.get("LOBE_HUMAN_CLIENT_ID", "").strip()
        human_scope = os.environ.get("LOBE_HUMAN_SCOPE", "lobe:verify").strip()
        token_client = info.get("client_id", "")
        token_scopes = info.get("scope", "").split() if isinstance(info.get("scope"), str) else []
        if (human_client_id and token_client == human_client_id) or (human_scope and human_scope in token_scopes):
            return True, info.get("username") or info.get("sub") or info.get("email")
    return False, None


def verify_status(lobe: str, slug: str) -> dict[str, Any]:
    """Current page_hash and whether the stored `verified:` stamp still matches it."""
    clean_slug = page_name(slug)
    page_file = l_paths(lobe)["WIKI"] / f"{clean_slug}.md"
    if not SLUG_RE.match(clean_slug) or not page_file.is_file():
        raise FileNotFoundError(f"Page '{clean_slug}' not found in lobe '{lobe}'")
    text = page_file.read_text(encoding="utf-8")
    current = page_hash(text)
    fm, _ = parse_frontmatter(text)
    v = fm.get("verified") if isinstance(fm, dict) else None
    is_verified = isinstance(v, dict) and bool(v.get("sha"))
    return {"lobe": lobe, "slug": clean_slug, "hash": current, "verified": v if is_verified else None,
            "is_verified": is_verified, "is_stale": is_verified and v["sha"] != current}


_VERIFY_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; color: #1f2328; }
h1 { font-size: 1.4rem; border-bottom: 1px solid #d0d7de; padding-bottom: 10px; }
.badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 0.85rem; font-weight: 600; }
.ok { background: #dafbe1; color: #1a7f37; } .stale { background: #ffebe9; color: #cf222e; } .unverified { background: #fff8c5; color: #9a6700; }
.box { background: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; margin: 20px 0; }
code { background: #eff1f3; padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 0.9em; }
button { background: #1f883d; color: white; border: none; padding: 8px 16px; border-radius: 6px; font-size: 1rem; cursor: pointer; }
"""


def _verify_html(title: str, body: str) -> str:
    return (f'<!DOCTYPE html>\n<html>\n<head>\n<meta charset="utf-8">\n<title>{html.escape(title)}</title>\n'
            f"<style>{_VERIFY_CSS}</style>\n</head>\n<body>\n{body}\n</body>\n</html>")


def _render_verify_html(st: dict[str, Any], token: str | None) -> str:
    """Status page with a one-click verify form; every value is escaped (frontmatter comes from git)."""
    e = lambda x: html.escape(str(x))  # noqa: E731
    v = st["verified"] or {}
    if st["is_stale"]:
        badge, desc = "stale", f"Reviewed by <code>{e(v.get('by'))}</code> on <code>{e(v.get('at'))}</code>; content changed since (reviewed hash <code>{e(v.get('sha'))}</code>)."
    elif st["is_verified"]:
        badge, desc = "ok", f"Verified by <code>{e(v.get('by'))}</code> on <code>{e(v.get('at'))}</code>."
    else:
        badge, desc = "unverified", "This page has not been human-reviewed yet."
    token_field = f'<input type="hidden" name="token" value="{e(token)}">' if token else ""
    return _verify_html(f"Verify {st['lobe']}/{st['slug']}", f"""<h1>Verify <code>{e(st['lobe'])} / {e(st['slug'])}</code></h1>
<div class="box">
<p>Current hash: <code>{e(st['hash'])}</code></p>
<p>Status: <span class="badge {badge}">{badge}</span></p>
<p>{desc}</p>
</div>
<form method="POST">
<input type="hidden" name="expected_hash" value="{e(st['hash'])}">{token_field}
<button type="submit">Verify this page</button>
</form>""")


def _render_verify_success_html(res: dict[str, Any]) -> str:
    e = lambda x: html.escape(str(x))  # noqa: E731
    v = res["verified"]
    return _verify_html("Page verified", f"""<h1>Verification confirmed</h1>
<div class="box">
<p>Page <code>{e(res['lobe'])} / {e(res['slug'])}</code> verified.</p>
<p>Hash <code>{e(v['sha'])}</code> &bull; reviewer <code>{e(v['by'])}</code> &bull; <code>{e(v['at'])}</code></p>
</div>""")


VERIFY_PATH_RE = re.compile(r"/api/lobes/[^/]+/pages/[^/]+/verify")


def dispatch_http(
    method: str,
    raw_path: str,
    body: bytes,
    auth_header: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query)

    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    is_verify = bool(VERIFY_PATH_RE.fullmatch(path))
    form: dict[str, str] = {}
    if is_verify and method == "POST" and hdrs.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
    # shortcut: a browser cannot send a Bearer header, so the verify page alone takes the token from
    # ?token= (GET) or its form (POST); the GET URL stays in browser history. Upgrade: an OAuth login flow.
    url_token = query.get("token", [None])[0] if method == "GET" else form.get("token")
    if is_verify and not auth_header and url_token:
        auth_header = f"Bearer {url_token}"

    if method == "GET" and path == "/health":
        return 200, {"status": "ok"}

    if method == "GET" and path.startswith("/.well-known/oauth-protected-resource") and oauth_issuer():
        return 200, protected_resource_metadata()

    if not check_auth(auth_header):
        return 401, {"error": "Unauthorized"}

    try:
        payload = form or (json.loads(body.decode("utf-8")) if body else {})
    except Exception:
        return 400, {"error": "Invalid JSON body"}

    try:
        if method == "POST" and path == "/mcp":
            resp = handle_mcp_message(payload)
            return (202, {}) if resp is None else (200, resp)
        if not isinstance(payload, dict):
            return 400, {"error": "Body must be a JSON object"}

        # REST routes map onto the MCP tools, so both share one argument handling
        flag = lambda k: query.get(k, ["false"])[0].lower() in ("1", "true", "yes")  # noqa: E731
        if path == "/api/lobes":
            if method == "GET":
                return 200, call_mcp_tool("lobe_list", {})
            if method == "POST":
                return 201, call_mcp_tool("lobe_create", payload)

        parts = [unquote(p) for p in path.strip("/").split("/")]
        # /api/lobes/{lobe}/...
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "lobes":
            lobe, sub, n = parts[2], parts[3], len(parts)

            if method == "GET" and sub == "export-okf" and n == 4:
                return 200, export_okf(lobe)

            # Verification endpoint: /api/lobes/{lobe}/pages/{slug}/verify
            if is_verify:
                wants_html = "text/html" in hdrs.get("accept", "")
                if method == "GET":
                    st = verify_status(lobe, parts[4])
                    return 200, _render_verify_html(st, url_token) if wants_html else st
                if method == "POST":
                    is_human, reviewer = check_human_auth(auth_header)
                    if not is_human:
                        return 403, {"error": "Forbidden: human verification credential required"}
                    if not payload.get("expected_hash"):
                        return 400, {"error": "Missing required field: expected_hash"}
                    # a named credential (OAuth) decides the reviewer; `by` only names the holder of a shared one
                    res = verify_page(lobe, parts[4], expected_hash=payload["expected_hash"], by=reviewer or payload.get("by"))
                    return 200, _render_verify_success_html(res) if wants_html else res

            route = {
                ("GET", "skills", 4): ("lobe_generate_skills", {"mode": query.get("mode", ["mcp"])[0]}),
                ("PUT", "remote", 4): ("lobe_configure_remote", payload),
                ("POST", "sync", 4): ("lobe_sync", payload),
                ("GET", "search", 4): ("lobe_search", {"terms": query.get("q", []), "no_decay": flag("no_decay")}),
                ("GET", "sections", 4): ("lobe_sections", {}),
                ("GET", "pending", 4): ("lobe_pending", {}),
                # `all` is REST-only, like export-okf: it also lists every missing source, which would fill an agent's context
                ("GET", "lint", 4): ("lobe_lint", {"strict": flag("strict"), "all": flag("all")}),
                ("GET", "links", 5): ("lobe_links", {"page": parts[-1]}),
                ("POST", "raw", 4): ("lobe_add_raw", payload),
                ("GET", "pages", 5): ("lobe_read_page", {
                    "slug": parts[-1], "section": query.get("section", [None])[0],
                    **{k: int(query[k][0]) for k in ("start_line", "end_line") if k in query}}),
                ("PUT", "pages", 5): ("lobe_write_page", {**payload, "slug": parts[-1]}),
                ("PATCH", "pages", 5): ("lobe_edit_page", {**payload, "slug": parts[-1]}),
            }.get((method, sub, n))
            if sub == "raw" and method == "GET" and n >= 5:
                route = ("lobe_read_raw", {"filename": "/".join(parts[4:])})
            if route:
                tool, args = route
                status = 201 if tool == "lobe_add_raw" else 200
                return status, call_mcp_tool(tool, {**args, "lobe": lobe})

        return 404, {"error": f"Not found: {method} {path}"}
    except KeyError as exc:
        return 400, {"error": f"Missing required field: {exc}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except FileNotFoundError as exc:
        return 404, {"error": str(exc)}
    except RuntimeError as exc:
        return 409, {"error": str(exc)}
    except Exception as exc:  # e.g. a failed git command: answer instead of dropping the connection
        return 500, {"error": f"{type(exc).__name__}: {exc}"}


MAX_BODY = 10 * 1024 * 1024  # far above any wiki page or raw source an agent sends


class LobeHTTPHandler(BaseHTTPRequestHandler):
    def _handle(self, method: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            # checked before the read: anyone can send a body, even without a token
            self.close_connection = True  # the unread body must not be parsed as the next request
            status, data = (413, {"error": f"Body over {MAX_BODY} bytes"}) if length > 0 else (400, {"error": "Invalid Content-Length"})
        else:
            body = self.rfile.read(length) if length else b""
            status, data = dispatch_http(
                method,
                self.path,
                body,
                self.headers.get("Authorization"),
                headers=dict(self.headers),
            )
        if isinstance(data, str):
            out = data.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            out = json.dumps(data).encode("utf-8")
            content_type = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(out)))
        if status == 401 and oauth_issuer():
            public = os.environ.get("LOBE_PUBLIC_URL", "").strip().rstrip("/")
            self.send_header(
                "WWW-Authenticate",
                f'Bearer resource_metadata="{public}/.well-known/oauth-protected-resource"',
            )
        if content_type.startswith("text/html"):
            self.send_header("Referrer-Policy", "no-referrer")  # the verify page URL may carry a token
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # keep server output quiet during requests/tests


def run_stdio() -> None:
    """Run MCP server over stdin/stdout (newline-delimited JSON-RPC)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle_mcp_message(json.loads(line))
        except ValueError:  # one bad line must not end the session for every later request
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error: invalid JSON"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def main() -> None:
    p = argparse.ArgumentParser(description="lobe-server — REST API & MCP server for lobe-forge")
    p.add_argument("--stdio", action="store_true", help="Run MCP server over stdio")
    p.add_argument("--host", default=os.environ.get("LOBE_HOST", "127.0.0.1"), help="Bind host")
    p.add_argument("--port", type=int, default=int(os.environ.get("LOBE_PORT", "8080")), help="Bind port")
    args = p.parse_args()

    if args.stdio:
        run_stdio()
    else:
        no_auth = not os.environ.get("LOBE_API_TOKEN", "").strip() and not oauth_issuer()
        if no_auth and args.host not in ("127.0.0.1", "localhost", "::1"):
            sys.exit(f"lobe-server: refusing to listen on {args.host} without authentication; "
                     "set LOBE_API_TOKEN or LOBE_OAUTH_ISSUER, or bind to 127.0.0.1")
        srv = ThreadingHTTPServer((args.host, args.port), LobeHTTPHandler)
        print(f"lobe-server listening on http://{args.host}:{args.port} (lobes={lobes_root()})", flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    main()
