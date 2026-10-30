#!/usr/bin/env python3
"""
lobe-server — Self-hosted REST API & MCP server for lobe-forge wikis.

Part 1: Core lobe management, wiki_tool.py execution, and local-first Git with optional remote push.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
FORGE_SCRIPT = ROOT / "scripts" / "forge.py"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def lobes_root() -> Path:
    path = Path(os.environ.get("LOBES_DIR", "/data/lobes")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_lobe(lobe: str) -> Path:
    if not NAME_RE.match(lobe):
        raise ValueError(f"Invalid lobe name: {lobe!r} (expected lowercase letters, digits, hyphens)")
    lobe_dir = (lobes_root() / lobe).resolve()
    if lobe_dir.parent != lobes_root() or not (lobe_dir / "wikictl").exists():
        raise FileNotFoundError(f"Lobe {lobe!r} not found in {lobes_root()}")
    return lobe_dir


def git_auth_args_and_env() -> tuple[list[str], dict[str, str]]:
    """Build non-interactive Git args and env, injecting LOBE_GIT_TOKEN for HTTP(S) remotes if set."""
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": os.environ.get("GIT_SSH_COMMAND", "ssh -o BatchMode=yes"),
    }
    extra_args: list[str] = []
    token = os.environ.get("LOBE_GIT_TOKEN", "").strip()
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        extra_args.extend(["-c", f"http.extraHeader=Authorization: Basic {basic}"])
    return extra_args, env


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    auth_args, env = git_auth_args_and_env()
    return subprocess.run(
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
        check=check,
    )


def verify_remote_access(remote_url: str) -> bool:
    """
    Verify read/access to `remote_url` via `git ls-remote --heads`.
    Raises ValueError if unreachable or unauthorized. Returns True if remote already has commits/branches.
    """
    auth_args, env = git_auth_args_and_env()
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


def git_commit_and_optional_push(repo: Path, message: str, paths: list[str] | None = None) -> dict[str, Any]:
    """
    Commit changes locally in `repo` (mandatory) and push to `origin` only if:
      1. A remote `origin` URL is configured on the repo, AND
      2. Push is enabled (`LOBE_GIT_PUSH` env var is "1"/"true" or `git config lobe.autopush` is "true").
    """
    if paths:
        git(repo, "add", "--", *paths)
    else:
        git(repo, "add", "-A")

    status = git(repo, "status", "--porcelain")
    committed = False
    commit_hash = None
    if status.stdout.strip():
        git(repo, "commit", "-q", "-m", message)
        committed = True
        commit_hash = git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    else:
        head = git(repo, "rev-parse", "--short", "HEAD", check=False)
        commit_hash = head.stdout.strip() if head.returncode == 0 else None

    remote_url = has_remote(repo)
    env_push = os.environ.get("LOBE_GIT_PUSH", "").lower() in ("1", "true", "yes")
    cfg_push = git(repo, "config", "--get", "lobe.autopush", check=False).stdout.strip().lower() == "true"
    should_push = bool(remote_url and (env_push or cfg_push))

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
        paths = l_paths(d.name)
        repo = paths["REPO"]
        purpose = ""
        lobe_md = d / "LOBE.md"
        if lobe_md.exists():
            for line in lobe_md.read_text(encoding="utf-8").splitlines():
                if line.startswith("**Purpose**:"):
                    purpose = line.split("**Purpose**:", 1)[1].strip()
                    break
        autopush = git(repo, "config", "--get", "lobe.autopush", check=False).stdout.strip().lower() == "true"
        items.append({
            "name": d.name,
            "purpose": purpose,
            "repo": str(repo),
            "remote": has_remote(repo),
            "autopush": autopush,
        })
    return items


def create_lobe(name: str, purpose: str, remote_url: str | None = None, autopush: bool = False) -> dict[str, Any]:
    if not NAME_RE.match(name):
        raise ValueError(f"Invalid lobe name: {name!r} (expected lowercase letters, digits, hyphens)")
    repo_dir = lobes_root() / name

    cloned = False
    if remote_url:
        remote_has_commits = verify_remote_access(remote_url)
        if remote_has_commits and not repo_dir.exists():
            auth_args, env = git_auth_args_and_env()
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
    return {"name": name, "purpose": purpose, "repo": str(repo_dir), "cloned": cloned, **git_info}


def configure_remote(
    lobe: str,
    remote_url: str | None,
    autopush: bool = True,
    verify: bool = True,
) -> dict[str, Any]:
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

    git(repo, "config", "lobe.autopush", "true" if (remote_url and autopush) else "false")
    return {"lobe": lobe, "remote": has_remote(repo), "autopush": bool(remote_url and autopush)}


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


def add_raw(lobe: str, filename: str, content: str) -> dict[str, Any]:
    safe_name = Path(filename).name
    if not safe_name or safe_name in (".", ".."):
        raise ValueError(f"Invalid raw filename: {filename!r}")
    paths = l_paths(lobe)
    raw_file = paths["RAW"] / safe_name
    raw_file.parent.mkdir(parents=True, exist_ok=True)
    raw_file.write_text(content, encoding="utf-8")
    rel = str(raw_file.relative_to(paths["REPO"]))
    git_info = git_commit_and_optional_push(paths["REPO"], f"raw: add {safe_name}", [rel])
    return {"lobe": lobe, "path": rel, **git_info}


def read_page(lobe: str, slug: str) -> dict[str, Any]:
    clean_slug = Path(slug).stem
    paths = l_paths(lobe)
    page_file = paths["WIKI"] / f"{clean_slug}.md"
    if not page_file.exists():
        raise FileNotFoundError(f"Page {clean_slug!r} not found in lobe {lobe!r}")
    return {
        "lobe": lobe,
        "slug": clean_slug,
        "path": str(page_file.relative_to(paths["REPO"])),
        "content": page_file.read_text(encoding="utf-8"),
    }


def write_page(
    lobe: str,
    slug: str,
    content: str,
    section: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    clean_slug = Path(slug).stem
    if not NAME_RE.match(clean_slug):
        raise ValueError(f"Invalid page slug: {slug!r} (expected lowercase letters, digits, hyphens)")
    paths = l_paths(lobe)
    page_file = paths["WIKI"] / f"{clean_slug}.md"
    is_new = not page_file.exists()
    page_file.write_text(content, encoding="utf-8")

    staged = [str(page_file.relative_to(paths["REPO"]))]
    if section and description:
        idx = run_wiki_tool(lobe, "index-add", "--section", section, "--page", clean_slug, "--description", description)
        if idx["returncode"] != 0:
            raise RuntimeError((idx["stderr"] or idx["stdout"]).strip())
        staged.append(str((paths["WIKI"] / "index.md").relative_to(paths["REPO"])))

    action = "create" if is_new else "update"
    git_info = git_commit_and_optional_push(paths["REPO"], f"wiki: {action} {clean_slug}", staged)
    return {"lobe": lobe, "slug": clean_slug, "created": is_new, **git_info}


# ── Part 2: MCP Tool Definitions & JSON-RPC 2.0 Dispatcher ──────────────────

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "lobe_list",
        "description": "List all lobes hosted on lobe-server and their Git remote status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "lobe_create",
        "description": "Create a new git-backed lobe wiki.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Lowercase hyphenated lobe name (e.g. hack, personal)."},
                "purpose": {"type": "string", "description": "One-line purpose of the lobe."},
                "remote_url": {"type": "string", "description": "Optional remote Git URL (origin)."},
                "autopush": {"type": "boolean", "description": "Whether to auto-push commits to origin."},
            },
            "required": ["name", "purpose"],
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
                "autopush": {"type": "boolean", "description": "Push automatically after local commits."},
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
        "name": "lobe_read_page",
        "description": "Read a markdown page from the lobe's wiki/ folder.",
        "inputSchema": {
            "type": "object",
            "properties": {"lobe": {"type": "string"}, "slug": {"type": "string"}},
            "required": ["lobe", "slug"],
        },
    },
    {
        "name": "lobe_write_page",
        "description": "Create or update a wiki page, optionally add to index.md, and commit locally (with optional push).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "lobe": {"type": "string"},
                "slug": {"type": "string"},
                "content": {"type": "string"},
                "section": {"type": "string", "description": "Optional index.md section for new pages."},
                "description": {"type": "string", "description": "Optional <=25-word index description."},
            },
            "required": ["lobe", "slug", "content"],
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
            autopush=bool(args.get("autopush", False)),
        )
    if name == "lobe_configure_remote":
        return configure_remote(
            lobe=args["lobe"],
            remote_url=args.get("remote_url"),
            autopush=bool(args.get("autopush", True)),
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
        cmd = ["search", *(["--no-decay"] if args.get("no_decay") else []), *terms]
        return run_wiki_tool(args["lobe"], *cmd)
    if name == "lobe_links":
        return run_wiki_tool(args["lobe"], "links", args["page"])
    if name == "lobe_sections":
        return run_wiki_tool(args["lobe"], "sections")
    if name == "lobe_pending":
        return run_wiki_tool(args["lobe"], "pending")
    if name == "lobe_lint":
        cmd = ["lint", *(["--strict"] if args.get("strict") else [])]
        return run_wiki_tool(args["lobe"], *cmd)
    if name == "lobe_add_raw":
        return add_raw(args["lobe"], args["filename"], args["content"])
    if name == "lobe_read_page":
        return read_page(args["lobe"], args["slug"])
    if name == "lobe_write_page":
        return write_page(
            lobe=args["lobe"],
            slug=args["slug"],
            content=args["content"],
            section=args.get("section"),
            description=args.get("description"),
        )
    raise ValueError(f"Unknown MCP tool: {name!r}")


def handle_mcp_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process a single JSON-RPC 2.0 MCP message and return a response dict (or None for notifications)."""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if "id" not in msg:
        return None  # JSON-RPC notification (e.g. notifications/initialized)

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "lobe-server", "version": "0.1.0"},
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

def check_auth(auth_header: str | None) -> bool:
    expected = os.environ.get("LOBE_API_TOKEN", "").strip()
    if not expected:
        return True
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    return auth_header.removeprefix("Bearer ").strip() == expected


def dispatch_http(method: str, raw_path: str, body: bytes, auth_header: str | None = None) -> tuple[int, Any]:
    parsed = urlparse(raw_path)
    path = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query)

    if method == "GET" and path == "/health":
        return 200, {"status": "ok"}

    if not check_auth(auth_header):
        return 401, {"error": "Unauthorized"}

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return 400, {"error": "Invalid JSON body"}

    try:
        if method == "POST" and path == "/mcp":
            resp = handle_mcp_message(payload)
            return (202, {}) if resp is None else (200, resp)

        if path == "/api/lobes":
            if method == "GET":
                return 200, {"lobes": list_lobes()}
            if method == "POST":
                return 201, create_lobe(
                    name=payload["name"],
                    purpose=payload["purpose"],
                    remote_url=payload.get("remote_url"),
                    autopush=bool(payload.get("autopush", False)),
                )

        parts = [unquote(p) for p in path.strip("/").split("/")]
        # /api/lobes/{lobe}/...
        if len(parts) >= 4 and parts[0] == "api" and parts[1] == "lobes":
            lobe = parts[2]
            sub = parts[3]

            if sub == "remote" and method == "PUT" and len(parts) == 4:
                return 200, configure_remote(
                    lobe=lobe,
                    remote_url=payload.get("remote_url"),
                    autopush=bool(payload.get("autopush", True)),
                )
            if sub == "sync" and method == "POST" and len(parts) == 4:
                return 200, sync_lobe(
                    lobe=lobe,
                    push=bool(payload.get("push", True)),
                )
            if sub == "search" and method == "GET" and len(parts) == 4:
                terms = query.get("q", [])
                if not terms:
                    return 400, {"error": "Missing query parameter 'q'"}
                no_decay = query.get("no_decay", ["false"])[0].lower() in ("1", "true", "yes")
                cmd = ["search", *(["--no-decay"] if no_decay else []), *terms]
                return 200, run_wiki_tool(lobe, *cmd)
            if sub == "sections" and method == "GET" and len(parts) == 4:
                return 200, run_wiki_tool(lobe, "sections")
            if sub == "pending" and method == "GET" and len(parts) == 4:
                return 200, run_wiki_tool(lobe, "pending")
            if sub == "lint" and method == "GET" and len(parts) == 4:
                strict = query.get("strict", ["false"])[0].lower() in ("1", "true", "yes")
                cmd = ["lint", *(["--strict"] if strict else [])]
                return 200, run_wiki_tool(lobe, *cmd)
            if sub == "links" and method == "GET" and len(parts) == 5:
                return 200, run_wiki_tool(lobe, "links", parts[4])
            if sub == "raw" and method == "POST" and len(parts) == 4:
                return 201, add_raw(lobe, payload["filename"], payload["content"])
            if sub == "pages" and len(parts) == 5:
                slug = parts[4]
                if method == "GET":
                    return 200, read_page(lobe, slug)
                if method == "PUT":
                    return 200, write_page(
                        lobe=lobe,
                        slug=slug,
                        content=payload["content"],
                        section=payload.get("section"),
                        description=payload.get("description"),
                    )

        return 404, {"error": f"Not found: {method} {path}"}
    except KeyError as exc:
        return 400, {"error": f"Missing required field: {exc}"}
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except FileNotFoundError as exc:
        return 404, {"error": str(exc)}
    except RuntimeError as exc:
        return 409, {"error": str(exc)}


class LobeHTTPHandler(BaseHTTPRequestHandler):
    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b""
        status, data = dispatch_http(method, self.path, body, self.headers.get("Authorization"))
        out = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # keep server output quiet during requests/tests


def run_stdio() -> None:
    """Run MCP server over stdin/stdout (newline-delimited JSON-RPC)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        resp = handle_mcp_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def main() -> None:
    p = argparse.ArgumentParser(description="lobe-server — REST API & MCP server for lobe-forge")
    p.add_argument("--stdio", action="store_true", help="Run MCP server over stdio")
    p.add_argument("--host", default=os.environ.get("LOBE_HOST", "0.0.0.0"), help="Bind host")
    p.add_argument("--port", type=int, default=int(os.environ.get("LOBE_PORT", "8080")), help="Bind port")
    args = p.parse_args()

    if args.stdio:
        run_stdio()
    else:
        srv = ThreadingHTTPServer((args.host, args.port), LobeHTTPHandler)
        print(f"lobe-server listening on http://{args.host}:{args.port} (lobes={lobes_root()})", flush=True)
        srv.serve_forever()


if __name__ == "__main__":
    main()
