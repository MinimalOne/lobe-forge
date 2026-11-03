#!/usr/bin/env python3
"""
End-to-end tests for server/app.py.
Run: python3 server/test_server.py
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app  # noqa: E402


SAMPLE_PAGE = """---
title: Raspberry Pi 5 Setup
summary: Setting up Raspberry Pi 5 with NVMe boot and local Git server.
aliases:
  - pi5 setup
  - blackpi setup
atoms:
  - "cmd: sudo rpi-eeprom-config --edit"
  - "decision: use local-first git commits with optional remote push"
sources:
  - raw/2026-10-30-pi5-notes.md
source_count: 1
provenance:
  - "E2E test"
last_updated: 2026-10-30
---

# Raspberry Pi 5 Setup

Configured [[pi5-setup]] from raw notes (source: 2026-10-30-pi5-notes.md).
"""


def test_part1_core_and_git_scenarios() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        lobes_dir = tmp_path / "lobes"
        os.environ["LOBES_DIR"] = str(lobes_dir)
        os.environ.pop("LOBE_GIT_PUSH", None)

        # 1. Create lobe (Scenario A: local-only, no remote configured)
        res = app.create_lobe("hack", "Homelab & device tinkering")
        assert res["name"] == "hack"
        assert res["remote"] is None
        assert res["pushed"] is False
        assert res["commit"] is not None

        # Invalid lobe name & unknown lobe validation
        for bad in ["Bad Name", "../escape", ""]:
            try:
                app.create_lobe(bad, "x")
                raise AssertionError(f"Expected ValueError for {bad!r}")
            except ValueError:
                pass

        try:
            app.resolve_lobe("nonexistent")
            raise AssertionError("Expected FileNotFoundError")
        except FileNotFoundError:
            pass

        # 2. List lobes
        lobes = app.list_lobes()
        assert len(lobes) == 1 and lobes[0]["name"] == "hack"
        assert lobes[0]["purpose"] == "Homelab & device tinkering"
        assert lobes[0]["remote"] is None and lobes[0]["autopush"] is False

        # 3. Add raw source -> commits locally, does not push, shows in pending
        raw_res = app.add_raw("hack", "2026-10-30-pi5-notes.md", "# Raw Pi 5 Notes\nBoot from NVMe.\n")
        assert raw_res["committed"] is True
        assert raw_res["pushed"] is False
        assert raw_res["path"] == "raw/2026-10-30-pi5-notes.md"

        # The server runs its own wiki_tool.py, not the lobe's copy that an image upgrade leaves behind
        lobe_copy = tmp_path / "lobes" / "hack" / "wiki_tool.py"
        lobe_copy_text = lobe_copy.read_text()
        lobe_copy.write_text("import sys; sys.exit('stale lobe copy ran')\n")
        pending = app.run_wiki_tool("hack", "pending")
        lobe_copy.write_text(lobe_copy_text)
        assert pending["returncode"] == 0 and "2026-10-30-pi5-notes.md" in pending["stdout"], pending

        # 4. Write wiki page + index-add -> commits locally, search & lint --strict pass
        page_res = app.write_page(
            "hack",
            "pi5-setup",
            SAMPLE_PAGE,
            section="General",
            description="Raspberry Pi 5 NVMe setup notes.",
        )
        assert page_res["created"] is True
        assert page_res["committed"] is True
        assert page_res["pushed"] is False

        read_back = app.read_page("hack", "pi5-setup")
        assert "Raspberry Pi 5 Setup" in read_back["content"]

        search_res = app.run_wiki_tool("hack", "search", "eeprom|nvme")
        assert search_res["returncode"] == 0 and "[[pi5-setup]]" in search_res["stdout"]
        assert "sudo rpi-eeprom-config --edit" in search_res["stdout"]

        lint_res = app.run_wiki_tool("hack", "lint", "--strict")
        assert lint_res["returncode"] == 0, lint_res["stdout"] + lint_res["stderr"]

        # 5. Scenario B: Configure remote with autopush=False -> local commit only, bare repo untouched
        bare_remote = tmp_path / "remote-hack.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare_remote)], check=True)

        cfg = app.configure_remote("hack", str(bare_remote), autopush=False)
        assert cfg["remote"] == str(bare_remote) and cfg["autopush"] is False

        raw_res2 = app.add_raw("hack", "2026-10-30-extra.md", "Extra local note.\n")
        assert raw_res2["committed"] is True and raw_res2["pushed"] is False
        remote_log = subprocess.run(
            ["git", "-C", str(bare_remote), "log", "--oneline"], capture_output=True, text=True
        )
        assert remote_log.returncode != 0  # Bare repo still has 0 pushed commits

        # 6. Scenario C: Enable autopush=True -> commits locally AND pushes to bare remote
        cfg_on = app.configure_remote("hack", str(bare_remote), autopush=True)
        assert cfg_on["autopush"] is True

        # the push runs after the write lock is released: another thread can write meanwhile
        real_push, lock_free = app.push_origin, []

        def spy_push(repo):
            def try_lock():
                lock_free.append(app._WRITE_LOCK.acquire(timeout=5))
                app._WRITE_LOCK.release()
            t = threading.Thread(target=try_lock)
            t.start()
            t.join()
            return real_push(repo)

        app.push_origin = spy_push
        try:
            raw_res3 = app.add_raw("hack", "2026-10-30-pushed.md", "Synced note.\n")
        finally:
            app.push_origin = real_push
        assert lock_free == [True], lock_free
        assert raw_res3["committed"] is True and raw_res3["pushed"] is True and raw_res3["push_error"] is None

        remote_log_after = subprocess.run(
            ["git", "-C", str(bare_remote), "log", "--oneline"], capture_output=True, text=True, check=True
        )
        assert "raw: add 2026-10-30-pushed.md" in remote_log_after.stdout
        assert "wiki: create pi5-setup" in remote_log_after.stdout

        # A rejected auto-push is reported, and the commit stays local until sync reconciles it
        other = tmp_path / "other-clone"
        subprocess.run(["git", "clone", "-q", str(bare_remote), str(other)], check=True)
        (other / "raw" / "2026-10-30-elsewhere.md").write_text("Pushed from another clone.\n")
        for cmd in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "elsewhere"], ["push", "-q"]):
            subprocess.run(["git", "-C", str(other), *cmd], check=True)
        rejected = app.add_raw("hack", "2026-10-30-rejected.md", "Push will be rejected.\n")
        assert rejected["committed"] is True and rejected["pushed"] is False, rejected
        assert rejected["push_error"] and "rejected" in rejected["push_error"], rejected
        assert "raw: add 2026-10-30-rejected.md" not in subprocess.run(
            ["git", "-C", str(bare_remote), "log", "--oneline"], capture_output=True, text=True, check=True).stdout
        synced = app.sync_lobe("hack")
        assert synced["pulled"] is True and synced["pushed"] is True, synced

        # 6b. A lobe's own autopush=false beats LOBE_GIT_PUSH=1; an unset one follows it
        os.environ["LOBE_GIT_PUSH"] = "1"
        try:
            remote_head = lambda: subprocess.run(  # noqa: E731
                ["git", "-C", str(bare_remote), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
            ).stdout
            before = remote_head()
            assert app.configure_remote("hack", str(bare_remote), autopush=False)["autopush"] is False
            assert app.add_raw("hack", "2026-10-30-optout.md", "Not pushed.\n")["pushed"] is False
            assert remote_head() == before
            assert [l["autopush"] for l in app.list_lobes() if l["name"] == "hack"] == [False]

            # null through REST and MCP unsets the lobe's value (not bool(None) == False), so the default applies
            code, rem = app.dispatch_http("PUT", "/api/lobes/hack/remote",
                                          json.dumps({"remote_url": str(bare_remote), "autopush": None}).encode())
            assert code == 200 and rem["autopush"] is True, rem
            assert app.git(app.l_paths("hack")["REPO"], "config", "--get", "lobe.autopush", check=False).returncode == 1
            assert app.add_raw("hack", "2026-10-30-default.md", "Pushed by default.\n")["pushed"] is True
            app.configure_remote("hack", str(bare_remote), autopush=False)
            mcp = app.handle_mcp_message({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
                "name": "lobe_configure_remote",
                "arguments": {"lobe": "hack", "remote_url": str(bare_remote), "autopush": None}}})
            assert mcp["result"]["structuredContent"]["autopush"] is True, mcp

            # git's own booleans (yes/no/on/off/1/0) count, not only true/false
            repo_dir = app.l_paths("hack")["REPO"]
            app.git(repo_dir, "config", "lobe.autopush", "no")
            assert app.autopush_enabled(repo_dir) is False
            app.git(repo_dir, "config", "lobe.autopush", "on")
            assert app.autopush_enabled(repo_dir) is True

            # lobe_create with an explicit autopush=false must not push its init commit
            empty_remote = tmp_path / "remote-optout.git"
            subprocess.run(["git", "init", "--bare", "-q", str(empty_remote)], check=True)
            created = app.create_lobe("optout", "Opt-out lobe", remote_url=str(empty_remote), autopush=False)
            assert created["pushed"] is False
            pushed_refs = subprocess.run(["git", "-C", str(empty_remote), "for-each-ref"], capture_output=True, text=True, check=True).stdout
            assert pushed_refs == "", pushed_refs
        finally:
            os.environ.pop("LOBE_GIT_PUSH", None)
        app.configure_remote("hack", str(bare_remote), autopush=True)

        # 7. Access check rejects unreachable/unauthorized remote_url on create_lobe and configure_remote
        bad_remote = str(tmp_path / "does-not-exist.git")
        try:
            app.create_lobe("fail-lobe", "Should fail", remote_url=bad_remote)
            raise AssertionError("Expected ValueError for unreachable remote_url")
        except ValueError as exc:
            assert "Cannot access remote repository" in str(exc)
        assert not (lobes_dir / "fail-lobe").exists()

        try:
            app.configure_remote("hack", bad_remote)
            raise AssertionError("Expected ValueError for unreachable remote_url")
        except ValueError as exc:
            assert "Cannot access remote repository" in str(exc)

        # Verify LOBE_GIT_TOKEN injects provider-appropriate HTTP Basic auth header for git operations
        import base64

        def decoded_header(remote_url: str | None = None) -> str:
            args, _ = app.git_auth_args_and_env(remote_url)
            b64 = [a.split("Basic ", 1)[1] for a in args if "Basic " in a][0]
            return base64.b64decode(b64).decode("utf-8")

        def with_host(host: str, url: str) -> str:
            os.environ["LOBE_GIT_TOKEN_HOST"] = host
            return decoded_header(url)

        os.environ["LOBE_GIT_TOKEN"] = "ghp_testtoken123"
        assert decoded_header("https://github.com/u/r.git") == "x-access-token:ghp_testtoken123"
        assert with_host("codeberg.org", "https://codeberg.org/u/r.git") == "x-access-token:ghp_testtoken123"
        assert with_host("gitlab.com", "https://gitlab.com/u/r.git") == "oauth2:ghp_testtoken123"
        assert with_host("bitbucket.org", "https://bitbucket.org/u/r.git") == "x-token-auth:ghp_testtoken123"

        os.environ["LOBE_GIT_TOKEN"] = "myuser:myapppass"
        assert with_host("bitbucket.org", "https://bitbucket.org/u/r.git") == "myuser:myapppass"
        os.environ.pop("LOBE_GIT_TOKEN_HOST")

        # The token goes only to the configured host (default github.com), scoped to that host's URL
        os.environ["LOBE_GIT_TOKEN"] = "ghp_testtoken123"
        for leak in ["https://evil.example/u/r.git", "https://github.com@evil.example/u/r.git",
                     "https://github.com.evil.example/u/r.git", "git@github.com:u/r.git", str(bad_remote)]:
            assert app.git_auth_args_and_env(leak)[0] == [], leak
        args, _ = app.git_auth_args_and_env("https://me@github.com/u/r.git")
        assert args[1].startswith("http.https://github.com/.extraHeader="), args

        # A failed git command reports git's message, never the token: local commands get no auth args
        app.create_lobe("leak", "Token leak test")
        leak_repo = app.l_paths("leak")["REPO"]
        # codeberg, not github: a developer's url.<ssh>.insteadOf for github.com would hide the leak
        os.environ["LOBE_GIT_TOKEN_HOST"] = "codeberg.org"
        app.configure_remote("leak", "https://codeberg.org/u/leak.git", autopush=False, verify=False)
        assert app.git_auth_args_and_env(app.has_remote(leak_repo))[0], "test must use a remote that gets the token"
        try:
            app.git(leak_repo, "commit", "-q", "-m", "nothing staged")
            raise AssertionError("Expected GitError for an empty commit")
        except app.GitError as exc:
            assert "ghp_testtoken123" not in str(exc) and "Basic" not in str(exc), str(exc)
            assert "nothing" in str(exc), str(exc)
        res = app.handle_mcp_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                      "params": {"name": "lobe_links", "arguments": {"lobe": "leak", "page": "x"}}})
        assert "Basic" not in json.dumps(res)
        os.environ.pop("LOBE_GIT_TOKEN_HOST")

        # LOBE_GIT_TOKEN_HOST may carry a scheme and port; a given port must match
        os.environ["LOBE_GIT_TOKEN_HOST"] = "https://git.internal:8443/"
        assert app.git_auth_args_and_env("https://git.internal:8443/u/r.git")[0]
        assert app.git_auth_args_and_env("https://git.internal/u/r.git")[0] == []
        os.environ["LOBE_GIT_TOKEN_HOST"] = "Codeberg.org"
        assert app.git_auth_args_and_env("https://codeberg.org:443/u/r.git")[0]
        os.environ.pop("LOBE_GIT_TOKEN_HOST")
        os.environ.pop("LOBE_GIT_TOKEN", None)

        # 8. Clone existing non-empty remote in create_lobe & explicit sync_lobe across two installations
        clone_res = app.create_lobe("hack-mirror", "Cloned lobe", remote_url=str(bare_remote), autopush=False)
        assert clone_res["cloned"] is True
        assert "Raspberry Pi 5 Setup" in app.read_page("hack-mirror", "pi5-setup")["content"]

        # Push a new raw file from 'hack' to bare_remote, then pull it into 'hack-mirror' via sync_lobe
        app.add_raw("hack", "2026-10-30-from-main.md", "New upstream content.\n")
        sync_res = app.sync_lobe("hack-mirror", push=False)
        assert sync_res["pulled"] is True and sync_res["pushed"] is False
        assert (lobes_dir / "hack-mirror" / "raw" / "2026-10-30-from-main.md").exists()


def http_req(
    base_url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        return err.code, json.loads(raw) if raw else {}


def test_part2_http_rest_and_mcp_e2e() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        lobes_dir = tmp_path / "lobes"
        os.environ["LOBES_DIR"] = str(lobes_dir)
        os.environ["LOBE_API_TOKEN"] = "secret-test-token"
        os.environ.pop("LOBE_GIT_PUSH", None)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.LobeHTTPHandler)
        port = srv.server_address[1]
        base_url = f"http://127.0.0.1:{port}"
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()

        try:
            # 1. Healthcheck is public; protected endpoints require Bearer token
            code, data = http_req(base_url, "GET", "/health")
            assert code == 200 and data == {"status": "ok"}

            code, _ = http_req(base_url, "GET", "/api/lobes")
            assert code == 401
            code, _ = http_req(base_url, "GET", "/api/lobes", token="wrong-token")
            assert code == 401
            code, _ = http_req(base_url, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})
            assert code == 401

            tok = "secret-test-token"

            # 2. Create two isolated lobes via REST API (one with generate_skills=True)
            code, h_res = http_req(
                base_url,
                "POST",
                "/api/lobes",
                {"name": "hack", "purpose": "Homelab tinkering", "generate_skills": True, "skill_mode": "mcp"},
                token=tok,
            )
            assert code == 201 and h_res["name"] == "hack" and h_res["pushed"] is False
            assert h_res["skill_mode"] == "mcp"
            assert h_res["skill_names"] == ["hack-add-raw", "hack-ingest", "hack-ask", "hack-lint"]
            assert "lobe_add_raw" in h_res["skills"]["hack-add-raw/SKILL.md"]
            assert "lobe_search" in h_res["skills"]["hack-ask/SKILL.md"]
            assert "{{" not in h_res["skills"]["hack-ingest/SKILL.md"]

            code, p_res = http_req(
                base_url, "POST", "/api/lobes", {"name": "personal", "purpose": "Personal notes"}, token=tok
            )
            assert code == 201 and p_res["name"] == "personal" and "skills" not in p_res

            # Generate skills later for an existing lobe via GET /api/lobes/{lobe}/skills
            code, sk_mcp = http_req(base_url, "GET", "/api/lobes/personal/skills?mode=mcp", token=tok)
            assert code == 200 and sk_mcp["mode"] == "mcp"
            assert "lobe_write_page" in sk_mcp["skills"]["personal-ingest/SKILL.md"]
            code, sk_loc = http_req(base_url, "GET", "/api/lobes/personal/skills?mode=local", token=tok)
            assert code == 200 and sk_loc["mode"] == "local"
            assert "wikictl" in sk_loc["skills"]["personal-ingest/SKILL.md"]

            code, list_res = http_req(base_url, "GET", "/api/lobes", token=tok)
            assert code == 200 and [x["name"] for x in list_res["lobes"]] == ["hack", "personal"]

            # 3. Add raw source, read raw source, check pending & sections, write page (with log_entry), read page
            code, raw_res = http_req(
                base_url,
                "POST",
                "/api/lobes/hack/raw",
                {"filename": "2026-10-30-pi5-notes.md", "content": "# Raw notes\n"},
                token=tok,
            )
            assert code == 201 and raw_res["committed"] is True

            code, raw_rd = http_req(base_url, "GET", "/api/lobes/hack/raw/2026-10-30-pi5-notes.md", token=tok)
            assert code == 200 and raw_rd["content"] == "# Raw notes\n"

            code, pend = http_req(base_url, "GET", "/api/lobes/hack/pending", token=tok)
            assert code == 200 and "2026-10-30-pi5-notes.md" in pend["stdout"]

            code, secs = http_req(base_url, "GET", "/api/lobes/hack/sections", token=tok)
            assert code == 200 and "General" in secs["stdout"]

            code, wr = http_req(
                base_url,
                "PUT",
                "/api/lobes/hack/pages/pi5-setup",
                {
                    "content": SAMPLE_PAGE,
                    "section": "General",
                    "description": "Pi 5 setup guide.",
                    "log_entry": "## 2026-10-30 — Ingest: 2026-10-30-pi5-notes.md\n\n- Created: `wiki/pi5-setup.md`",
                },
                token=tok,
            )
            assert code == 200 and wr["created"] is True and wr["committed"] is True
            hack_logs = [lobes_dir / "hack" / "wiki" / "log.md", *(lobes_dir / "hack" / "wiki" / "log").glob("*.md")]
            assert any("Ingest: 2026-10-30-pi5-notes.md" in f.read_text(encoding="utf-8") for f in hack_logs)  # may rotate out by date

            code, rd = http_req(base_url, "GET", "/api/lobes/hack/pages/pi5-setup", token=tok)
            assert code == 200 and rd["slug"] == "pi5-setup" and "sudo rpi-eeprom-config" in rd["content"]

            # 3b. Partial read, PATCH edit, and stale-sha conflict over REST
            code, part = http_req(base_url, "GET", "/api/lobes/hack/pages/pi5-setup?start_line=1&end_line=2", token=tok)
            assert code == 200 and part["content"] == "---\ntitle: Raspberry Pi 5 Setup\n" and part["end_line"] == 2
            code, ed = http_req(
                base_url,
                "PATCH",
                "/api/lobes/hack/pages/pi5-setup",
                {"edits": [{"op": "append", "content": "PATCHed line."}], "expected_sha": rd["sha"]},
                token=tok,
            )
            assert code == 200 and ed["committed"] is True and ed["sha"] != rd["sha"]
            code, stale = http_req(
                base_url,
                "PATCH",
                "/api/lobes/hack/pages/pi5-setup",
                {"edits": [{"op": "append", "content": "lost"}], "expected_sha": rd["sha"]},
                token=tok,
            )
            assert code == 409 and "changed since it was read" in stale["error"]
            code, bad_idx = http_req(
                base_url,
                "PUT",
                "/api/lobes/hack/pages/new-page",
                {"content": "x", "section": "No Such Section", "description": "x"},
                token=tok,
            )
            assert code == 400 and "not found" in bad_idx["error"]

            # 4. Search isolation (hit in hack, zero bleed into personal), links, and strict lint
            code, sr_hack = http_req(base_url, "GET", "/api/lobes/hack/search?q=eeprom|nvme", token=tok)
            assert code == 200 and "[[pi5-setup]]" in sr_hack["stdout"]

            code, sr_pers = http_req(base_url, "GET", "/api/lobes/personal/search?q=eeprom|nvme", token=tok)
            assert code == 200 and "[[pi5-setup]]" not in sr_pers["stdout"]

            code, lnk = http_req(base_url, "GET", "/api/lobes/hack/links/pi5-setup", token=tok)
            assert code == 200 and "[[pi5-setup]]" in lnk["stdout"]

            code, lnt = http_req(base_url, "GET", "/api/lobes/hack/lint?strict=true", token=tok)
            assert code == 200 and lnt["returncode"] == 0

            # 5. Remote push via REST API
            bare_remote = tmp_path / "rest-remote.git"
            subprocess.run(["git", "init", "--bare", "-q", str(bare_remote)], check=True)
            code, rem = http_req(
                base_url,
                "PUT",
                "/api/lobes/hack/remote",
                {"remote_url": str(bare_remote), "autopush": True},
                token=tok,
            )
            assert code == 200 and rem["autopush"] is True

            code, wr2 = http_req(
                base_url,
                "PUT",
                "/api/lobes/hack/pages/pi5-setup",
                {"content": SAMPLE_PAGE + "\nUpdated line.\n"},
                token=tok,
            )
            assert code == 200 and wr2["created"] is False and wr2["pushed"] is True

            # 5b. Clone existing remote via POST /api/lobes, test POST /api/lobes/{lobe}/sync & conflict safety
            code, bad_rem = http_req(
                base_url,
                "PUT",
                "/api/lobes/personal/remote",
                {"remote_url": str(tmp_path / "no-access.git")},
                token=tok,
            )
            assert code == 400 and "Cannot access remote repository" in bad_rem["error"]

            code, cl_res = http_req(
                base_url,
                "POST",
                "/api/lobes",
                {"name": "hack-clone", "purpose": "Cloned via REST", "remote_url": str(bare_remote), "autopush": False},
                token=tok,
            )
            assert code == 201 and cl_res["cloned"] is True

            # Push a change from 'hack' to bare_remote, then sync 'hack-clone' via REST
            http_req(
                base_url,
                "POST",
                "/api/lobes/hack/raw",
                {"filename": "2026-10-30-synced.md", "content": "Synced via REST.\n"},
                token=tok,
            )
            code, sync_res = http_req(
                base_url, "POST", "/api/lobes/hack-clone/sync", {"push": False}, token=tok
            )
            assert code == 200 and sync_res["pulled"] is True and sync_res["pushed"] is False
            assert (lobes_dir / "hack-clone" / "raw" / "2026-10-30-synced.md").exists()

            # Conflict safety: edit same line in 'hack' (pushed) and 'hack-clone' (local), verify 409 & clean abort
            http_req(
                base_url,
                "PUT",
                "/api/lobes/hack/pages/pi5-setup",
                {"content": SAMPLE_PAGE + "\nConflict line from hack.\n"},
                token=tok,
            )
            http_req(
                base_url,
                "PUT",
                "/api/lobes/hack-clone/pages/pi5-setup",
                {"content": SAMPLE_PAGE + "\nConflict line from hack-clone.\n"},
                token=tok,
            )
            code, conf_res = http_req(
                base_url, "POST", "/api/lobes/hack-clone/sync", {"push": True}, token=tok
            )
            assert code == 409 and "rebase aborted" in conf_res["error"]
            _, clean_page = http_req(base_url, "GET", "/api/lobes/hack-clone/pages/pi5-setup", token=tok)
            assert "<<<<<<<" not in clean_page["content"]

            # 6. Error responses (404, 400)
            assert http_req(base_url, "GET", "/api/lobes/unknown/sections", token=tok)[0] == 404
            assert http_req(base_url, "GET", "/api/lobes/hack/pages/missing", token=tok)[0] == 404
            assert http_req(base_url, "GET", "/api/lobes/hack/search", token=tok)[0] == 400
            assert http_req(base_url, "GET", "/api/unknown", token=tok)[0] == 404

            # 7. MCP over Streamable HTTP (POST /mcp)
            code, init_resp = http_req(
                base_url, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, token=tok
            )
            assert code == 200 and init_resp["result"]["serverInfo"]["name"] == "lobe-server"
            # version negotiation: echo a supported requested version, otherwise answer with the newest
            def negotiated(params: Any) -> str:
                return app.handle_mcp_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params})["result"]["protocolVersion"]
            assert init_resp["result"]["protocolVersion"] == app.MCP_PROTOCOL_VERSIONS[-1]
            for v in app.MCP_PROTOCOL_VERSIONS:
                assert negotiated({"protocolVersion": v}) == v
            for other in ({"protocolVersion": "2099-01-01"}, {"protocolVersion": 7}, {}, ["not", "an", "object"]):
                assert negotiated(other) == app.MCP_PROTOCOL_VERSIONS[-1], other

            code, notif_resp = http_req(
                base_url, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=tok
            )
            assert code == 202

            code, tools_resp = http_req(
                base_url, "POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=tok
            )
            tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
            assert code == 200 and {
                "lobe_list",
                "lobe_create",
                "lobe_generate_skills",
                "lobe_sync",
                "lobe_search",
                "lobe_read_raw",
                "lobe_write_page",
                "lobe_lint",
            } <= tool_names

            code, sk_call = http_req(
                base_url,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 25,
                    "method": "tools/call",
                    "params": {"name": "lobe_generate_skills", "arguments": {"lobe": "hack", "mode": "mcp"}},
                },
                token=tok,
            )
            assert code == 200 and sk_call["result"]["isError"] is False
            assert "hack-ask/SKILL.md" in sk_call["result"]["structuredContent"]["skills"]

            code, rraw_call = http_req(
                base_url,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 26,
                    "method": "tools/call",
                    "params": {"name": "lobe_read_raw", "arguments": {"lobe": "hack", "filename": "2026-10-30-pi5-notes.md"}},
                },
                token=tok,
            )
            assert code == 200 and rraw_call["result"]["isError"] is False
            assert rraw_call["result"]["structuredContent"]["content"] == "# Raw notes\n"

            code, call_resp = http_req(
                base_url,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "lobe_search", "arguments": {"lobe": "hack", "terms": ["eeprom"]}},
                },
                token=tok,
            )
            assert code == 200 and call_resp["result"]["isError"] is False
            assert "[[pi5-setup]]" in call_resp["result"]["content"][0]["text"]

            code, err_call = http_req(
                base_url,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {"name": "lobe_read_page", "arguments": {"lobe": "hack", "slug": "missing"}},
                },
                token=tok,
            )
            assert code == 200 and err_call["result"]["isError"] is True

            code, edit_call = http_req(
                base_url,
                "POST",
                "/mcp",
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "lobe_edit_page",
                        "arguments": {
                            "lobe": "hack",
                            "slug": "pi5-setup",
                            "edits": [{"op": "find_replace", "find": "Conflict line from hack.", "content": "MCP-edited line."}],
                        },
                    },
                },
                token=tok,
            )
            assert code == 200 and edit_call["result"]["isError"] is False, edit_call
            assert "MCP-edited line." in (lobes_dir / "hack" / "wiki" / "pi5-setup.md").read_text(encoding="utf-8")

            # 8. MCP over stdio (--stdio subprocess)
            stdio_input = "\n".join([
                json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialize"}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                "{not json",  # bad lines get an error reply and the session goes on
                "[1, 2]",
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 11,
                    "method": "tools/call",
                    "params": {"name": "lobe_list", "arguments": {}},
                }),
            ]) + "\n"
            proc = subprocess.run(
                [sys.executable, str(Path(app.__file__).resolve()), "--stdio"],
                input=stdio_input,
                capture_output=True,
                text=True,
                env={**os.environ, "LOBES_DIR": str(lobes_dir)},
                check=True,
            )
            lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
            assert len(lines) == 4, lines
            assert lines[0]["id"] == 10 and lines[0]["result"]["serverInfo"]["name"] == "lobe-server"
            assert [l["error"]["code"] for l in lines[1:3]] == [-32700, -32600]
            assert lines[3]["id"] == 11 and len(lines[3]["result"]["structuredContent"]["lobes"]) == 3
        finally:
            srv.shutdown()
            srv.server_close()
            os.environ.pop("LOBE_API_TOKEN", None)


def test_part3_container_isolation_and_compose() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    dockerfile = (repo_root / "server" / "Dockerfile").read_text(encoding="utf-8")
    compose = (repo_root / "server" / "docker-compose.yml").read_text(encoding="utf-8")
    openapi = (repo_root / "openapi.yaml").read_text(encoding="utf-8")

    assert "openapi: 3.1.0" in openapi
    for route in [
        "/health:",
        "/api/lobes:",
        "/api/lobes/{lobe}/skills:",
        "/api/lobes/{lobe}/remote:",
        "/api/lobes/{lobe}/sync:",
        "/api/lobes/{lobe}/search:",
        "/api/lobes/{lobe}/sections:",
        "/api/lobes/{lobe}/pending:",
        "/api/lobes/{lobe}/lint:",
        "/api/lobes/{lobe}/links/{page}:",
        "/api/lobes/{lobe}/raw:",
        "/api/lobes/{lobe}/raw/{filename}:",
        "/api/lobes/{lobe}/pages/{slug}:",
        "/mcp:",
    ]:
        assert route in openapi, f"Missing route {route} in openapi.yaml"

    assert "COPY .claude-plugin/plugin.json /app/.claude-plugin/plugin.json" in dockerfile
    assert "COPY scripts/ /app/scripts/" in dockerfile
    assert "COPY templates/ /app/templates/" in dockerfile
    assert "COPY server/app.py /app/server/app.py" in dockerfile
    assert 'profiles: ["webui"]' in compose
    assert 'profiles: ["forgejo"]' in compose

    expected_ver = json.loads((repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    assert app.server_version() == expected_ver

    # Simulate exact Dockerfile layout in an isolated temp root (/app) and run MCP stdio E2E
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        app_root = Path(tmp) / "app"
        lobes_dir = Path(tmp) / "data" / "lobes"
        (app_root / ".claude-plugin").mkdir(parents=True)
        shutil.copy2(
            repo_root / ".claude-plugin" / "plugin.json",
            app_root / ".claude-plugin" / "plugin.json",
        )
        shutil.copytree(repo_root / "scripts", app_root / "scripts")
        shutil.copytree(repo_root / "templates", app_root / "templates")
        (app_root / "server").mkdir(parents=True)
        shutil.copy2(repo_root / "server" / "app.py", app_root / "server" / "app.py")

        stdio_reqs = "\n".join([
            json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "lobe_create", "arguments": {"name": "box", "purpose": "Container lobe"}},
            }),
            json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "lobe_lint", "arguments": {"lobe": "box", "strict": True}},
            }),
        ]) + "\n"

        proc = subprocess.run(
            [sys.executable, str(app_root / "server" / "app.py"), "--stdio"],
            input=stdio_reqs,
            capture_output=True,
            text=True,
            env={**os.environ, "LOBES_DIR": str(lobes_dir)},
            check=True,
        )
        resps = [json.loads(line) for line in proc.stdout.strip().splitlines()]
        assert len(resps) == 2
        assert resps[0]["result"]["isError"] is False
        assert resps[0]["result"]["structuredContent"]["name"] == "box"
        assert resps[1]["result"]["isError"] is False
        assert resps[1]["result"]["structuredContent"]["returncode"] == 0

        # HTTP mode refuses a non-loopback address when no authentication is configured
        no_auth = {k: v for k, v in os.environ.items() if k not in ("LOBE_API_TOKEN", "LOBE_OAUTH_ISSUER")}
        refused = subprocess.run(
            [sys.executable, str(app_root / "server" / "app.py"), "--host", "0.0.0.0", "--port", "0"],
            capture_output=True, text=True, env={**no_auth, "LOBES_DIR": str(lobes_dir)}, timeout=30,
        )
        assert refused.returncode != 0 and "refusing to listen" in refused.stderr, refused.stderr

    assert '"127.0.0.1:${LOBE_PORT:-8080}:8080"' in compose


def test_part4_oauth_resource_server() -> None:
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import parse_qs

    class FakeIdP(BaseHTTPRequestHandler):
        def _send(self, data: dict[str, Any]) -> None:
            out = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self) -> None:
            self._send({"introspection_endpoint": f"{idp_url}/introspect/"})

        def do_POST(self) -> None:
            if self.headers.get("Authorization") != "Basic bG9iZTpzM2NyZXQ=":  # lobe:s3cret
                self._send({"active": False})
                return
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            token = parse_qs(body)["token"][0]
            self._send({
                "good-token": {"active": True, "client_id": "lobe"},
                "other-app-token": {"active": True, "client_id": "vaultwarden"},
            }.get(token, {"active": False}))

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    idp = ThreadingHTTPServer(("127.0.0.1", 0), FakeIdP)
    idp_url = f"http://127.0.0.1:{idp.server_address[1]}"
    threading.Thread(target=idp.serve_forever, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.LobeHTTPHandler)
    base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = {
        "LOBE_OAUTH_ISSUER": f"{idp_url}/application/o/lobe/",
        "LOBE_OAUTH_CLIENT_ID": "lobe",
        "LOBE_OAUTH_CLIENT_SECRET": "s3cret",
        "LOBE_PUBLIC_URL": "https://lobes.example.com",
        "LOBE_API_TOKEN": "static-token",
    }
    os.environ.update(env)
    app.introspection_endpoint.cache_clear()
    app._TOKEN_CACHE.clear()
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            os.environ["LOBES_DIR"] = tmp
            code, meta = http_req(base_url, "GET", "/.well-known/oauth-protected-resource/mcp")
            assert code == 200
            assert meta["resource"] == "https://lobes.example.com/mcp"
            assert meta["authorization_servers"] == [env["LOBE_OAUTH_ISSUER"]]

            req = urllib.request.Request(f"{base_url}/mcp", data=b"{}", method="POST")
            try:
                urllib.request.urlopen(req)
                raise AssertionError("expected 401")
            except urllib.error.HTTPError as err:
                assert err.code == 401
                assert err.headers["WWW-Authenticate"] == (
                    'Bearer resource_metadata="https://lobes.example.com/.well-known/oauth-protected-resource"'
                )

            assert http_req(base_url, "GET", "/api/lobes", token="good-token")[0] == 200
            assert http_req(base_url, "GET", "/api/lobes", token="static-token")[0] == 200
            assert http_req(base_url, "GET", "/api/lobes", token="other-app-token")[0] == 401
            assert http_req(base_url, "GET", "/api/lobes", token="bogus")[0] == 401
            assert "good-token" in app._TOKEN_CACHE and "bogus" not in app._TOKEN_CACHE

            # OAuth alone (no static token) must still enforce auth.
            os.environ.pop("LOBE_API_TOKEN")
            assert http_req(base_url, "GET", "/api/lobes")[0] == 401
            assert http_req(base_url, "GET", "/api/lobes", token="good-token")[0] == 200
    finally:
        srv.shutdown()
        idp.shutdown()
        for k in [*env, "LOBES_DIR"]:
            os.environ.pop(k, None)


def test_part5_partial_reads_edits_and_write_lock() -> None:
    page = SAMPLE_PAGE + """
## Setup

Flash the EEPROM.

```bash
## not a heading
```

## Notes

Old note.
"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["LOBES_DIR"] = str(Path(tmp) / "lobes")
        os.environ.pop("LOBE_GIT_PUSH", None)
        app.create_lobe("edits", "Edit tests")
        w = app.write_page("edits", "pi5-setup", page, section="General", description="Pi notes.")
        page_file = Path(tmp) / "lobes" / "edits" / "wiki" / "pi5-setup.md"

        full = app.read_page("edits", "pi5-setup")
        assert "generated: { by: \"mcp-client\"" in full["content"] and full["sha"] == w["sha"] and full["lines"] == len(page.splitlines()) + 1

        # Section read stops at the next real `## ` heading, not one inside a code fence
        setup = app.read_page("edits", "pi5-setup", section="setup")
        assert setup["content"].startswith("## Setup\n") and "## not a heading" in setup["content"]
        assert "Old note." not in setup["content"]
        assert app.read_page("edits", "pi5-setup", section="Notes")["content"] == "## Notes\n\nOld note.\n"
        rng = app.read_page("edits", "pi5-setup", start_line=setup["start_line"], end_line=setup["start_line"])
        assert rng["content"] == "## Setup\n" and rng["sections"] == ["Setup", "Notes"]
        assert "sections" not in full
        crlf = app.h2_spans(["---\r\n", "## yaml\r\n", "---\r\n", "## Real\r\n"])
        assert [n for n, _, _ in crlf] == ["Real"]
        for bad in [{"section": "Missing"}, {"start_line": 9999}, {"start_line": 1, "end_line": 0}, {"start_line": 0}]:
            try:
                app.read_page("edits", "pi5-setup", **bad)
                raise AssertionError(f"Expected ValueError for {bad}")
            except ValueError:
                pass

        # Edits: all applied in one commit, sections stay well-formed
        index_file = page_file.parent / "index.md"
        index_file.write_text("".join(l for l in index_file.read_text().splitlines(True) if "[[pi5-setup]]" not in l))
        app.git(page_file.parents[1], "commit", "-qam", "test: drop index row")
        head = lambda: app.git(page_file.parents[1], "rev-parse", "HEAD").stdout.strip()  # noqa: E731
        before = head()
        res = app.edit_page(
            "edits",
            "pi5-setup",
            [
                {"op": "find_replace", "find": "source_count: 1", "content": "source_count: 2"},
                {"op": "append", "section": "Setup", "content": "Then reboot."},
                {"op": "replace_section", "section": "Notes", "content": "New note."},
                {"op": "append", "content": "Tail line."},
            ],
            expected_sha=full["sha"],
            index_section="General",
            index_description="Pi notes, edited.",
        )
        text = page_file.read_text(encoding="utf-8")
        assert res["committed"] is True and res["sha"] == app.page_sha(text)
        assert app.git(page_file.parents[1], "rev-list", "--count", f"{before}..HEAD").stdout.strip() == "1"
        assert "source_count: 2" in text and "Old note." not in text
        assert "Pi notes, edited." in index_file.read_text(encoding="utf-8")
        assert "```\nThen reboot.\n\n## Notes\n\nNew note.\nTail line.\n" in text, text

        # A failing edit aborts the whole batch; a stale sha is refused
        after = head()
        for edits, sha, exc in [
            ([{"op": "append", "content": "x"}, {"op": "find_replace", "find": "nope", "content": "y"}], None, ValueError),
            ([{"op": "find_replace", "find": "a", "content": "b", "expected_count": 1}], None, ValueError),
            ([{"op": "append", "content": "x"}], full["sha"], RuntimeError),
            ([{"op": "delete", "content": ""}], None, ValueError),
            ([{"op": "append", "content": None}], None, ValueError),
            ([{"op": "find_replace", "find": "Tail line.", "content": "x", "expected_count": None}], None, None),
            ([{"op": "find_replace", "find": "Tail line.", "content": "x", "expected_count": 0}], None, ValueError),
            (["not-an-object"], None, ValueError),
            ("not-a-list", None, ValueError),
        ]:
            if exc is None:  # null expected_count means the default of 1: validate it without committing
                assert app.apply_edit(text, edits[0]).endswith("x\n")
                continue
            try:
                app.edit_page("edits", "pi5-setup", edits, expected_sha=sha)
                raise AssertionError(f"Expected {exc.__name__} for {edits}")
            except exc:
                pass
        assert page_file.read_text(encoding="utf-8") == text and head() == after
        try:
            app.write_page("edits", "pi5-setup", "clobber", expected_sha=full["sha"])
            raise AssertionError("Expected RuntimeError for stale write")
        except RuntimeError:
            pass
        assert page_file.read_text(encoding="utf-8") == text

        # A bad index section fails before anything is written (an already-indexed page skips index-add)
        try:
            app.write_page("edits", "brand-new", "clobber", section="No Such Section", description="x")
            raise AssertionError("Expected ValueError for unknown index section")
        except ValueError as exc:
            assert "not found" in str(exc)
            pass
        assert not (page_file.parent / "brand-new.md").exists()
        assert app.git(page_file.parents[1], "status", "--porcelain").stdout == ""

        # A write with log_entry rotates earlier months out of log.md, in the same commit
        log_file = page_file.parent / "log.md"
        log_file.write_text(log_file.read_text() + "\n## 2020-01-02 — Ingest: old\n\n- old\n")
        app.git(page_file.parents[1], "commit", "-qam", "test: old log entry")
        r = app.edit_page("edits", "pi5-setup", [{"op": "append", "content": "Rotated."}], log_entry="## 2099-01-01 — Ingest: new\n\n- new")
        assert r["committed"] and "Ingest: old" not in log_file.read_text() and "Ingest: new" in log_file.read_text()
        assert "Ingest: old" in (page_file.parent / "log" / "2020-01.md").read_text()
        assert "\n\n\n" not in log_file.read_text(), repr(log_file.read_text())
        assert app.git(page_file.parents[1], "status", "--porcelain").stdout == ""
        text, after = page_file.read_text(encoding="utf-8"), app.git(page_file.parents[1], "rev-parse", "HEAD").stdout.strip()

        # Parallel writers: every commit lands, none fails on git's index.lock
        errors: list[Exception] = []

        def save(i: int) -> None:
            try:
                app.add_raw("edits", f"2026-11-01-n{i}.md", f"note {i}\n")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        # Nested raw sources: every path lobe_pending prints can be read; nothing outside the raw folder can
        lobes = Path(tmp) / "lobes"
        (lobes / "edits" / "raw" / "sub").mkdir(parents=True, exist_ok=True)
        (lobes / "edits" / "raw" / "sub" / "n.md").write_text("nested\n")
        assert "raw/sub/n.md" in app.run_wiki_tool("edits", "pending")["stdout"]
        for name in ("raw/sub/n.md", "sub/n.md"):
            got = app.read_raw("edits", name)
            assert got["content"] == "nested\n" and got["filename"] == "sub/n.md" and got["path"] == "raw/sub/n.md", got
        assert app.dispatch_http("GET", "/api/lobes/edits/raw/raw/sub/n.md", b"")[1]["content"] == "nested\n"
        (lobes / "outside.md").write_text("secret\n")
        (lobes / "edits" / "raw" / "link.md").symlink_to(lobes / "outside.md")
        for bad in ("../LOBE.md", "raw/../../outside.md", str(lobes / "outside.md"), "", "raw", "link.md"):
            try:
                app.read_raw("edits", bad)
                raise AssertionError(f"read_raw accepted {bad!r}")
            except ValueError:
                pass
        assert app.dispatch_http("GET", "/api/lobes/edits/raw/..%2F..%2Foutside.md", b"")[0] == 400
        (lobes / "edits" / "raw" / "link.md").unlink()
        (lobes / "outside.md").unlink()
        # an adopted llmwiki/docs layout: lobe_pending prints docs/..., which read_raw takes as is
        subprocess.run([sys.executable, str(app.FORGE_SCRIPT), "create", "--name", "adopted", "--purpose", "Docs layout",
                        "--repo", str(lobes / "adopted"), "--wiki-dir", "llmwiki", "--raw-dir", "docs"], check=True, capture_output=True)
        (lobes / "adopted" / "docs" / "team").mkdir(parents=True)
        (lobes / "adopted" / "docs" / "team" / "runbook.md").write_text("runbook\n")
        assert "docs/team/runbook.md" in app.run_wiki_tool("adopted", "pending")["stdout"]
        assert app.read_raw("adopted", "docs/team/runbook.md")["content"] == "runbook\n"

        # One lobe-name rule (forge's): rejected before anything touches the disk, as a 400
        for bad_name in ("2fa", "a" * 31):
            code, body = app.dispatch_http("POST", "/api/lobes", json.dumps({"name": bad_name, "purpose": "x"}).encode())
            assert code == 400 and "Invalid lobe name" in body["error"], (code, body)
            assert not (lobes / bad_name).exists()
        assert app.resolve_lobe("adopted") == (lobes / "adopted").resolve()

        # Stalled network git fails instead of holding the write lock forever
        _, env = app.git_auth_args_and_env("https://github.com/u/r.git")
        assert env["GIT_HTTP_LOW_SPEED_LIMIT"] == "1" and env["GIT_HTTP_LOW_SPEED_TIME"] == "30"
        assert "ConnectTimeout=10" in env["GIT_SSH_COMMAND"]

        # Saving unchanged content while an unrelated file is untracked commits nothing and does not fail
        stray = page_file.parents[1] / "raw" / "stray.md"
        stray.write_text("made by another tool\n")
        unchanged = page_file.read_text(encoding="utf-8")
        status, body = app.dispatch_http("PUT", "/api/lobes/edits/pages/pi5-setup", json.dumps({"content": unchanged}).encode())
        assert status == 200 and body["committed"] is False, (status, body)
        stray.unlink()

        threads = [threading.Thread(target=save, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        assert app.git(page_file.parents[1], "rev-list", "--count", f"{after}..HEAD").stdout.strip() == "8"

        # A dot in a page name is part of the name: k8s-1.29-upgrade must not open k8s-1
        wiki = page_file.parent
        (wiki / "k8s-1.md").write_text("short\n")
        (wiki / "k8s-1.29-upgrade.md").write_text(SAMPLE_PAGE.replace("Configured", "Use git push --force. Configured"))
        for slug in ("k8s-1.29-upgrade", "k8s-1.29-upgrade.md", "wiki/k8s-1.29-upgrade.md"):
            got = app.read_page("edits", slug)
            assert got["slug"] == "k8s-1.29-upgrade" and "--force" in got["content"], (slug, got["slug"])
        try:
            app.write_page("edits", "k8s-1.v2", "clobber")
            raise AssertionError("write_page accepted a dotted slug")
        except ValueError:
            pass
        assert (wiki / "k8s-1.md").read_text() == "short\n"
        # ...but an existing dotted page can be edited and rewritten, by MCP and REST, without touching k8s-1
        app.git(wiki.parent, "add", "-A")
        app.git(wiki.parent, "commit", "-qm", "test: dotted page")
        res = app.call_mcp_tool("lobe_edit_page", {"lobe": "edits", "slug": "k8s-1.29-upgrade",
                                                   "edits": [{"op": "find_replace", "find": "Configured", "content": "Set up"}]})
        assert res["slug"] == "k8s-1.29-upgrade" and res["created"] is False, res
        text = (wiki / "k8s-1.29-upgrade.md").read_text()
        assert "Set up" in text and "--force" in text, text
        status, body = app.dispatch_http("PUT", "/api/lobes/edits/pages/k8s-1.29-upgrade",
                                         json.dumps({"content": text.replace("Set up", "Configured")}).encode())
        assert status == 200 and body["created"] is False, body
        assert "Configured" in (wiki / "k8s-1.29-upgrade.md").read_text()
        assert (wiki / "k8s-1.md").read_text() == "short\n"
        assert not (wiki / "k8s-1.v2.md").exists()

        # Terms and page names starting with "-" are searched for, not parsed as wikictl options
        res = app.call_mcp_tool("lobe_search", {"lobe": "edits", "terms": ["--force"]})
        assert res["returncode"] == 0 and "k8s-1.29-upgrade" in res["stdout"], res
        status, body = app.dispatch_http("GET", "/api/lobes/edits/search?q=--force", b"")
        assert status == 200 and "k8s-1.29-upgrade" in body["stdout"], body
        assert app.call_mcp_tool("lobe_links", {"lobe": "edits", "page": "-x"})["returncode"] == 1  # not found, not a usage error (2)

        # raw/ is append-only: a second save under the same name is refused and the first stays
        raw_dir = Path(tmp) / "lobes" / "edits" / "raw"
        status, body = app.dispatch_http("POST", "/api/lobes/edits/raw", json.dumps({"filename": "2026-11-01-n0.md", "content": "other\n"}).encode())
        assert status == 409 and "already exists" in body["error"], (status, body)
        status, _ = app.dispatch_http("POST", "/api/lobes/edits/raw", json.dumps({"filename": "deep/2026-11-01-n0.md", "content": "x\n"}).encode())
        assert status == 409 and (raw_dir / "2026-11-01-n0.md").read_text() == "note 0\n"
        assert app.dispatch_http("POST", "/api/lobes/edits/raw", b"[1, 2]")[0] == 400  # not a JSON object
        status, body = app.dispatch_http("POST", "/mcp", b"[1, 2]")
        assert status == 200 and body["error"]["code"] == -32600, (status, body)
        # params that is not an object is answered, not raised (over stdio a raise ends the session)
        resp = app.handle_mcp_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": [1]})
        assert resp["id"] == 9 and resp["result"]["isError"] is True, resp

        # A write that fails midway (here: text that cannot be encoded) leaves the old page whole
        before = (wiki / "k8s-1.29-upgrade.md").read_text()
        try:
            app.write_page("edits", "k8s-1.29-upgrade", "broken \ud800 text\n")
            raise AssertionError("unencodable content accepted")
        except UnicodeEncodeError:
            pass
        assert (wiki / "k8s-1.29-upgrade.md").read_text() == before
        assert not list(wiki.glob("*.tmp")), list(wiki.glob("*.tmp"))

        # lobe_search honours limit (the ask and ingest skills pass 5)
        res = app.call_mcp_tool("lobe_search", {"lobe": "edits", "terms": ["."], "limit": 1})
        assert res["returncode"] == 0 and "\n1 of " in res["stdout"], res

        # A search regex that backtracks without end is killed instead of pinning a CPU
        app.write_page("edits", "redos", "a" * 40 + "!\n")
        app.WIKI_TOOL_TIMEOUT = 1
        try:
            started = time.time()
            status, body = app.dispatch_http("GET", "/api/lobes/edits/search?q=" + quote("(a+)+$"), b"")
            assert status == 409 and "timed out" in body["error"], (status, body)
            assert time.time() - started < 10
        finally:
            app.WIKI_TOOL_TIMEOUT = 30

        # REST routes go through the MCP tools: same purpose, same skills, same errors
        assert app.dispatch_http("GET", "/api/lobes", b"")[1]["lobes"][0]["purpose"] == "Docs layout"
        rest = app.dispatch_http("GET", "/api/lobes/edits/skills", b"")[1]
        assert rest == app.call_mcp_tool("lobe_generate_skills", {"lobe": "edits"}) and rest["purpose"] == "Edit tests"
        assert app.dispatch_http("GET", "/api/lobes/edits/search", b"")[0] == 400
        assert app.dispatch_http("GET", "/api/lobes/nope/sections", b"")[0] == 404

    # Oversized or malformed bodies are refused before they are read, even without a token
    import http.client
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.LobeHTTPHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for length, want in ((str(app.MAX_BODY + 1), 413), ("4000000000", 413), ("abc", 400), ("-5", 400)):
            conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            conn.putrequest("POST", "/mcp")
            conn.putheader("Content-Length", length)
            conn.endheaders()  # no body sent: a server that tried to read it would time out here
            assert conn.getresponse().status == want, length
            conn.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_part6_h2_spans_and_masker() -> None:
    text = """---
title: Test Headings
---

# Title

## Section Alpha

Some text in alpha.

```python
## Not A Heading in backticks
print("hello")
```

~~~markdown
## Not A Heading in tildes
```python
nested fence
```
~~~

````
## Not A Heading in 4-backtick fence
```
inner
```
````

    ## Not A Heading in 4-space indent
    code = 1

## Section Beta

Some text in beta.
"""
    lines = text.splitlines(keepends=True)
    spans = app.h2_spans(lines)
    assert len(spans) == 2, f"Expected 2 spans, got {spans}"
    assert spans[0][0] == "Section Alpha", spans[0]
    assert spans[1][0] == "Section Beta", spans[1]

    s, e = app.section_span(lines, "Section Alpha")
    sec_alpha = "".join(lines[s:e])
    assert "Section Alpha" in sec_alpha
    assert "Section Beta" not in sec_alpha
    assert "Not A Heading in backticks" in sec_alpha

    s, e = app.section_span(lines, "Section Beta")
    sec_beta = "".join(lines[s:e])
    assert "Section Beta" in sec_beta
    assert "Section Alpha" not in sec_beta


def test_part7_server_owned_trust_fields() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["LOBES_DIR"] = str(Path(tmp) / "lobes")
        os.environ.pop("LOBE_GIT_PUSH", None)
        app.create_lobe("trust", "Trust tests")

        # 1. Write page with faked client trust fields and actor="agent-42"
        faked_inbound = """---
title: Trust Test Page
summary: Testing server trust fields.
generated: { by: "faked-client", at: "1970-01-01" }
verified:
  by: fake-verifier
  sha: 00000000
---

# Trust Test Page Body
"""
        res1 = app.write_page("trust", "trust-page", faked_inbound, section="General", description="Trust page.", actor="agent-42")
        read1 = app.read_page("trust", "trust-page")

        # Returned sha must match read_page sha (the file on disk)
        assert res1["sha"] == read1["sha"]
        assert "fake-verifier" not in read1["content"]
        assert "faked-client" not in read1["content"]
        assert 'generated: { by: "agent-42"' in read1["content"]

        # 2. Simulate human verification on disk
        page_file = Path(tmp) / "lobes" / "trust" / "wiki" / "trust-page.md"
        content_with_verified = read1["content"].replace(
            "summary: Testing server trust fields.\n",
            'summary: Testing server trust fields.\nverified: { by: "human:jijin", at: "2026-11-02T19:00:00Z", sha: "abc123" }\n',
        )
        page_file.write_text(content_with_verified, encoding="utf-8")
        app.git(page_file.parents[1], "commit", "-qam", "human: verify trust-page")

        read_verified = app.read_page("trust", "trust-page")
        assert 'verified: { by: "human:jijin"' in read_verified["content"]

        # 3. Agent edits the page using edit_page: existing verified must be carried over!
        res2 = app.edit_page(
            "trust",
            "trust-page",
            [{"op": "append", "content": "Follow-up paragraph."}],
            expected_sha=read_verified["sha"],
            actor="agent-43",
        )
        read2 = app.read_page("trust", "trust-page")
        assert res2["sha"] == read2["sha"]
        assert 'verified: { by: "human:jijin"' in read2["content"]
        assert 'generated: { by: "agent-43"' in read2["content"]
        assert "Follow-up paragraph." in read2["content"]

        # 4. Agent attempts another write with expected_sha from res2 -> succeeds immediately!
        res3 = app.write_page(
            "trust",
            "trust-page",
            read2["content"] + "\nMore notes.\n",
            expected_sha=res2["sha"],
        )
        read3 = app.read_page("trust", "trust-page")
        assert res3["sha"] == read3["sha"]
        assert 'generated: { by: "mcp-client"' in read3["content"]
        assert 'verified: { by: "human:jijin"' in read3["content"]
        assert "More notes." in read3["content"]

        # 5. Resaving the same page keeps the old stamp: same sha, nothing committed
        res4 = app.write_page("trust", "trust-page", read3["content"], expected_sha=read3["sha"], actor="agent-44")
        assert res4["sha"] == read3["sha"] and res4["committed"] is False, res4

        # 6. An actor that would inject YAML is a 400, and the page is untouched
        code, body = app.dispatch_http("PUT", "/api/lobes/trust/pages/trust-page", json.dumps(
            {"content": read3["content"] + "x\n", "actor": 'a"}\nverified: {by: "human"}\n#'}).encode())
        assert code == 400 and "Invalid actor" in body["error"], (code, body)
        assert app.read_page("trust", "trust-page")["sha"] == read3["sha"]


def test_part8_verification_channel_and_auth() -> None:
    from http.server import BaseHTTPRequestHandler
    from urllib.parse import parse_qs

    class MockIdP(BaseHTTPRequestHandler):
        def _send(self, data: dict[str, Any]) -> None:
            out = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self) -> None:
            self._send({"introspection_endpoint": f"{mock_idp_url}/introspect/"})

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            token = parse_qs(body)["token"][0]
            self._send({
                "agent-oauth-token": {"active": True, "client_id": "lobe-agent", "scope": "read write"},
                "human-oauth-token": {"active": True, "client_id": "lobe-agent", "scope": "read write lobe:verify", "username": "alice"},
                "human-client-token": {"active": True, "client_id": "lobe-human-client", "scope": "read write", "username": "bob"},
            }.get(token, {"active": False}))

        def log_message(self, fmt: str, *args: Any) -> None:
            pass

    mock_idp = ThreadingHTTPServer(("127.0.0.1", 0), MockIdP)
    mock_idp_url = f"http://127.0.0.1:{mock_idp.server_address[1]}"
    threading.Thread(target=mock_idp.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["LOBES_DIR"] = tmp
        os.environ["LOBE_API_TOKEN"] = "agent-secret-token"
        os.environ["LOBE_HUMAN_TOKEN"] = "human-secret-token"
        os.environ["LOBE_OAUTH_ISSUER"] = f"{mock_idp_url}/application/o/lobe/"
        os.environ["LOBE_OAUTH_CLIENT_ID"] = "lobe-agent"
        os.environ["LOBE_OAUTH_CLIENT_SECRET"] = "lobe-s3cret"
        os.environ["LOBE_HUMAN_CLIENT_ID"] = "lobe-human-client"
        os.environ["LOBE_HUMAN_SCOPE"] = "lobe:verify"
        app.introspection_endpoint.cache_clear()
        app._TOKEN_CACHE.clear()

        # 1. Create a lobe and a test page
        app.create_lobe("verlobe", "Verification test lobe")
        app.write_page(
            "verlobe",
            "verify-test",
            "---\ntitle: Verify Test\nsummary: Summary\nlast_updated: 2026-11-02\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom fact\n---\n\n## Section\n\nOriginal content.\n",
        )
        paths = app.l_paths("verlobe")
        page_file = paths["WIKI"] / "verify-test.md"
        actual_page_hash = app.page_hash(page_file.read_text(encoding="utf-8"))

        # 2. GET verify status (unverified)
        code, status_data = app.dispatch_http("GET", "/api/lobes/verlobe/pages/verify-test/verify", b"", "Bearer agent-secret-token")
        assert code == 200
        assert status_data["is_verified"] is False
        assert status_data["is_stale"] is False
        assert status_data["hash"] == actual_page_hash

        # 3. GET HTML confirmation page
        code, html_data = app.dispatch_http(
            "GET",
            "/api/lobes/verlobe/pages/verify-test/verify",
            b"",
            "Bearer human-secret-token",
            headers={"Accept": "text/html"},
        )
        assert code == 200
        assert isinstance(html_data, str)
        assert "<!DOCTYPE html>" in html_data
        assert ">unverified<" in html_data

        # 4. Auth gating on POST /api/lobes/{lobe}/pages/{slug}/verify
        # 4a. No token -> 401
        code, body = app.dispatch_http("POST", "/api/lobes/verlobe/pages/verify-test/verify", json.dumps({"expected_hash": actual_page_hash}).encode())
        assert code == 401

        # 4b. Agent token (LOBE_API_TOKEN) -> 403 Forbidden!
        code, body = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            json.dumps({"expected_hash": actual_page_hash}).encode(),
            "Bearer agent-secret-token",
        )
        assert code == 403
        assert "Forbidden" in body["error"]

        # 4c. Agent OAuth token without human scope -> 403 Forbidden!
        code, body = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            json.dumps({"expected_hash": actual_page_hash}).encode(),
            "Bearer agent-oauth-token",
        )
        assert code == 403

        # 4d. Human token, but expected_sha mismatch -> 409 Conflict
        code, body = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            json.dumps({"expected_hash": "wrong_hash_1234"}).encode(),
            "Bearer human-secret-token",
        )
        assert code == 409
        assert "does not match expected_hash" in body["error"]

        # 4e. Human token, correct expected_sha -> 200 OK!
        code, body = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            json.dumps({"expected_hash": actual_page_hash, "by": "jijin"}).encode(),
            "Bearer human-secret-token",
        )
        assert code == 200
        assert body["verified"]["sha"] == actual_page_hash
        assert body["verified"]["by"] == "human:jijin"
        assert body["committed"] is True

        # Check page on disk has verified field
        disk_text = page_file.read_text(encoding="utf-8")
        assert 'verified: { by: "human:jijin", at:' in disk_text
        assert f'sha: "{actual_page_hash}"' in disk_text

        # 5. GET verify status now returns verified and not stale
        code, status_data = app.dispatch_http("GET", "/api/lobes/verlobe/pages/verify-test/verify", b"", "Bearer agent-secret-token")
        assert code == 200
        assert status_data["is_verified"] is True
        assert status_data["is_stale"] is False

        # 6. Browser flow: GET the page with ?token=, then POST its form with the token in the body
        form_hdrs = {"Accept": "text/html", "Content-Type": "application/x-www-form-urlencoded"}
        code, html_get = app.dispatch_http(
            "GET", "/api/lobes/verlobe/pages/verify-test/verify?token=human-secret-token", b"", headers={"Accept": "text/html"})
        assert code == 200 and 'name="token" value="human-secret-token"' in html_get, html_get
        code, html_post = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            f"expected_hash={actual_page_hash}&by=jijin&token=human-secret-token".encode(),
            headers=form_hdrs,
        )
        assert code == 200 and isinstance(html_post, str) and "Verification confirmed" in html_post, html_post
        # The token is not taken from the URL on POST, nor anywhere outside the verify page
        code, _ = app.dispatch_http(
            "POST", "/api/lobes/verlobe/pages/verify-test/verify?token=human-secret-token",
            f"expected_hash={actual_page_hash}".encode(), headers=form_hdrs)
        assert code == 401
        assert app.dispatch_http("GET", "/api/lobes?token=agent-secret-token", b"")[0] == 401
        # Form bodies are only read on the verify route
        code, body = app.dispatch_http("POST", "/api/lobes", b"name=x&purpose=y", "Bearer agent-secret-token",
                                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert code == 400 and "Invalid JSON" in body["error"], (code, body)
        assert app.dispatch_http("POST", "/api/lobes", b"\xff\xfe", "Bearer agent-secret-token")[0] == 400

        # Agents cannot verify over MCP: the tool does not exist
        rpc = lambda m, p: json.dumps({"jsonrpc": "2.0", "id": 9, "method": m, "params": p}).encode()  # noqa: E731
        _, tools = app.dispatch_http("POST", "/mcp", rpc("tools/list", {}), "Bearer agent-secret-token")
        assert "lobe_verify_page" not in {x["name"] for x in tools["result"]["tools"]}
        _, res = app.dispatch_http("POST", "/mcp", rpc("tools/call", {"name": "lobe_verify_page", "arguments": {
            "lobe": "verlobe", "slug": "verify-test", "expected_hash": actual_page_hash, "by": "mallory"}}), "Bearer agent-secret-token")
        assert "mallory" not in page_file.read_text(encoding="utf-8"), res

        # 7. Human OAuth token verification (scope lobe:verify); its identity beats a claimed `by`
        code, body = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            json.dumps({"expected_hash": actual_page_hash, "by": "mallory"}).encode(),
            "Bearer human-oauth-token",
        )
        assert code == 200
        assert body["verified"]["by"] == "human:alice"

        # 8. Human OAuth client verification (client_id lobe-human-client)
        code, body = app.dispatch_http(
            "POST",
            "/api/lobes/verlobe/pages/verify-test/verify",
            json.dumps({"expected_hash": actual_page_hash}).encode(),
            "Bearer human-client-token",
        )
        assert code == 200
        assert body["verified"]["by"] == "human:bob"

        # 9. Now agent modifies the content of verify-test using edit_page
        app.edit_page(
            "verlobe",
            "verify-test",
            [{"op": "append", "content": "Tampered agent line."}],
            expected_sha=body["sha"],
            actor="agent-42",
        )
        tampered_text = page_file.read_text(encoding="utf-8")
        assert "Tampered agent line." in tampered_text
        # Verified block was preserved by edit_page
        assert 'verified: { by: "human:bob"' in tampered_text

        # Check GET verify status: page is now STALE
        code, status_data = app.dispatch_http("GET", "/api/lobes/verlobe/pages/verify-test/verify", b"", "Bearer agent-secret-token")
        assert code == 200
        assert status_data["is_verified"] is True
        assert status_data["is_stale"] is True

        # Check lint detects the stale verification
        lint_res = app.call_mcp_tool("lobe_lint", {"lobe": "verlobe", "strict": True})
        assert lint_res["returncode"] == 0  # soft warning
        assert "content changed since review by human:bob" in lint_res["stdout"]

        # 9b. A dotted page (readable since PR #19, not creatable by write_page) can be verified
        dotted = paths["WIKI"] / "k8s-1.29.md"
        dotted.write_text(page_file.read_text(encoding="utf-8").replace("verified:", "x-old:"), encoding="utf-8")
        code, st = app.dispatch_http("GET", "/api/lobes/verlobe/pages/k8s-1.29/verify", b"", "Bearer agent-secret-token")
        assert code == 200 and st["slug"] == "k8s-1.29" and st["is_verified"] is False, (code, st)
        code, res = app.dispatch_http("POST", "/api/lobes/verlobe/pages/k8s-1.29/verify",
                                      json.dumps({"expected_hash": st["hash"], "by": "bob"}).encode(), "Bearer human-secret-token")
        assert code == 200 and res["verified"]["sha"] == st["hash"], (code, res)
        assert "verified:" in dotted.read_text(encoding="utf-8")
        for bad in ("..", ".hidden", "k8s-1.", "a..b"):
            code, _ = app.dispatch_http("GET", f"/api/lobes/verlobe/pages/{bad}/verify", b"", "Bearer agent-secret-token")
            assert code == 404, (bad, code)

        # 10. Frontmatter from git is escaped in the HTML page
        text = page_file.read_text(encoding="utf-8").replace('by: "human:bob"', 'by: "<script>alert(1)</script>"')
        page_file.write_text(text, encoding="utf-8")
        _, html_xss = app.dispatch_http("GET", "/api/lobes/verlobe/pages/verify-test/verify", b"",
                                        "Bearer agent-secret-token", headers={"Accept": "text/html"})
        assert "<script>" not in html_xss and "&lt;script&gt;" in html_xss, html_xss

        # 11. Over real HTTP: the body is written once, and HTML carries no-referrer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), app.LobeHTTPHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def raw_get(path: str, accept: str = "*/*") -> bytes:
                sock = socket.create_connection(srv.server_address)
                sock.sendall(f"GET {path} HTTP/1.0\r\nAccept: {accept}\r\n\r\n".encode())
                out = b""
                while chunk := sock.recv(65536):
                    out += chunk
                sock.close()
                return out
            head_, _, body_ = raw_get("/health").partition(b"\r\n\r\n")
            assert body_ == b'{"status": "ok"}', body_
            head_, _, body_ = raw_get("/api/lobes/verlobe/pages/verify-test/verify?token=human-secret-token", "text/html").partition(b"\r\n\r\n")
            assert b"Referrer-Policy: no-referrer" in head_ and body_.count(b"<!DOCTYPE html>") == 1, head_
        finally:
            srv.shutdown()


def test_part9_page_types_and_lifecycle() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["LOBES_DIR"] = tmp
        os.environ.pop("LOBE_API_TOKEN", None)
        os.environ.pop("LOBE_HUMAN_TOKEN", None)
        os.environ.pop("LOBE_OAUTH_ISSUER", None)

        app.create_lobe("typelobe", "Type test lobe")

        # 1. Page without type triggers lint T warning
        app.write_page(
            "typelobe",
            "untyped-page",
            "---\ntitle: Untyped\nsummary: Test summary.\nlast_updated: 2026-11-01\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom fact\n---\n\n## Section\n\nContent.\n",
        )
        lint1 = app.call_mcp_tool("lobe_lint", {"lobe": "typelobe", "strict": True})
        assert lint1["returncode"] == 0
        assert "T. Page types (missing or invalid type)" in lint1["stdout"]
        assert "untyped-page.md — missing frontmatter 'type'" in lint1["stdout"]

        # 2. An agent adds the type by rewriting the page (no server-side set-type)
        typed = app.read_page("typelobe", "untyped-page")
        app.write_page("typelobe", "untyped-page", typed["content"].replace("title: Untyped\n", "title: Untyped\ntype: Note\n"),
                       expected_sha=typed["sha"])
        lint2 = app.call_mcp_tool("lobe_lint", {"lobe": "typelobe", "strict": True})
        assert "T. Page types" not in lint2["stdout"], lint2["stdout"]

        # 3. A page written today is fresh even with an old last_updated: the edit restarts the clock
        old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
        page = f"---\ntitle: Backup Runbook\ntype: Runbook\nsummary: Backup steps.\nlast_updated: {old_date}\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom fact\n---\n\n# Runbook\n"
        app.write_page("typelobe", "backup-runbook", page)
        lint3 = app.call_mcp_tool("lobe_lint", {"lobe": "typelobe", "strict": True})
        assert "backup-runbook.md — expired on" not in lint3["stdout"], lint3["stdout"]

        # 4. A past stale_after expires it; lint S is a warning, not a failure
        app.write_page("typelobe", "backup-runbook", page.replace("type: Runbook\n", "type: Runbook\nstale_after: 2020-01-01\n"),
                       expected_sha=app.read_page("typelobe", "backup-runbook")["sha"])
        lint4 = app.call_mcp_tool("lobe_lint", {"lobe": "typelobe", "strict": True})
        assert lint4["returncode"] == 0
        assert "S. Stale pages (past stale_after date)" in lint4["stdout"]
        assert "backup-runbook.md — expired on 2020-01-01" in lint4["stdout"], lint4["stdout"]

        # 5. A future stale_after clears it, and no lint step left the lobe repo dirty
        app.write_page("typelobe", "backup-runbook", page.replace("type: Runbook\n", "type: Runbook\nstale_after: 2099-01-01\n"),
                       expected_sha=app.read_page("typelobe", "backup-runbook")["sha"])
        lint5 = app.call_mcp_tool("lobe_lint", {"lobe": "typelobe", "strict": True})
        assert "backup-runbook.md — expired on" not in lint5["stdout"]
        dirty = app.git(app.l_paths("typelobe")["REPO"], "status", "--porcelain", "--", "wiki")
        assert dirty.stdout.strip() == "", dirty.stdout


def test_part10_export_okf() -> None:
    """Test Part 10: OKF v0.2 bundle export via the REST endpoint (no MCP tool)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        token = "test-export-token-12345"
        os.environ["LOBES_DIR"] = tmp_dir
        os.environ["LOBE_API_TOKEN"] = token
        os.environ.pop("LOBE_HUMAN_TOKEN", None)
        os.environ.pop("LOBE_OAUTH_ISSUER", None)

        # 1. Create a lobe
        app.create_lobe("okflobe", "OKF export test lobe")

        # 2. Write a page with frontmatter, wiki links, anchors, and code blocks
        page_content = """---
title: Export Runbook
type: Runbook
summary: Backup operations guide.
stale_after: 2026-12-31
atoms:
  - "cmd: backup.sh"
aliases:
  - backup-ops
---

# Export Runbook

See [[cluster-setup|Cluster Setup]] and [[index#overview]].

```bash
# Code block should preserve inner links
echo "[[not-a-link]]"
```

Inline `[[inline-code]]` check.
"""
        app.write_page("okflobe", "export-runbook", page_content, section="General", description="Backup operations")

        # 3. The export is REST only: an agent cannot pull the whole wiki in one MCP call
        rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        _, tools = app.dispatch_http("POST", "/mcp", rpc, f"Bearer {token}")
        assert "lobe_export_okf" not in {t["name"] for t in tools["result"]["tools"]}
        status, res = app.dispatch_http("GET", "/api/lobes/okflobe/export-okf", b"", f"Bearer {token}")
        assert status == 200 and res["lobe"] == "okflobe"
        assert res["okf_version"] == "0.2"
        assert res["page_count"] >= 2
        assert "index.md" in res["files"]
        assert "export-runbook.md" in res["files"]
        assert "log.md" not in res["files"], sorted(res["files"])

        rb = res["files"]["export-runbook.md"]
        assert "description: Backup operations guide." in rb
        assert "summary:" not in rb
        assert "stale_after: 2026-12-31T00:00:00Z" in rb
        assert "[Cluster Setup](/cluster-setup.md)" in rb
        assert "[index](/index.md#overview)" in rb
        assert "last_updated: " not in rb or "T00:00:00Z" in rb
        assert 'echo "[[not-a-link]]"' in rb
        assert "`[[inline-code]]`" in rb

        idx = res["files"]["index.md"]
        assert 'okf_version: "0.2"' in idx

        # 4. The endpoint needs auth like every other route
        assert app.dispatch_http("GET", "/api/lobes/okflobe/export-okf", b"")[0] == 401


if __name__ == "__main__":
    test_part1_core_and_git_scenarios()
    print("OK: Part 1 core & Git E2E scenarios passed")
    test_part2_http_rest_and_mcp_e2e()
    print("OK: Part 2 HTTP REST & MCP (HTTP + stdio) E2E scenarios passed")
    test_part3_container_isolation_and_compose()
    print("OK: Part 3 container isolation & compose verification passed")
    test_part4_oauth_resource_server()
    print("OK: Part 4 OAuth resource server passed")
    test_part5_partial_reads_edits_and_write_lock()
    print("OK: Part 5 partial reads, edits & write lock passed")
    test_part6_h2_spans_and_masker()
    print("OK: Part 6 h2_spans & code masker passed")
    test_part7_server_owned_trust_fields()
    print("OK: Part 7 server-owned trust fields passed")
    test_part8_verification_channel_and_auth()
    print("OK: Part 8 verification channel and auth passed")
    test_part9_page_types_and_lifecycle()
    print("OK: Part 9 page types and lifecycle passed")
    test_part10_export_okf()
    print("OK: Part 10 OKF v0.2 export passed")
