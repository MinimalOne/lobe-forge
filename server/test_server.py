#!/usr/bin/env python3
"""
End-to-end tests for server/app.py.
Run: python3 server/test_server.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

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
    with tempfile.TemporaryDirectory() as tmp:
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

        pending = app.run_wiki_tool("hack", "pending")
        assert pending["returncode"] == 0 and "2026-10-30-pi5-notes.md" in pending["stdout"]

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

        raw_res3 = app.add_raw("hack", "2026-10-30-pushed.md", "Synced note.\n")
        assert raw_res3["committed"] is True and raw_res3["pushed"] is True and raw_res3["push_error"] is None

        remote_log_after = subprocess.run(
            ["git", "-C", str(bare_remote), "log", "--oneline"], capture_output=True, text=True, check=True
        )
        assert "raw: add 2026-10-30-pushed.md" in remote_log_after.stdout
        assert "wiki: create pi5-setup" in remote_log_after.stdout

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

        os.environ["LOBE_GIT_TOKEN"] = "ghp_testtoken123"
        assert decoded_header("https://github.com/u/r.git") == "x-access-token:ghp_testtoken123"
        assert decoded_header("https://codeberg.org/u/r.git") == "x-access-token:ghp_testtoken123"
        assert decoded_header("https://gitlab.com/u/r.git") == "oauth2:ghp_testtoken123"
        assert decoded_header("https://bitbucket.org/u/r.git") == "x-token-auth:ghp_testtoken123"

        os.environ["LOBE_GIT_TOKEN"] = "myuser:myapppass"
        assert decoded_header("https://bitbucket.org/u/r.git") == "myuser:myapppass"
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
    with tempfile.TemporaryDirectory() as tmp:
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

            # 2. Create two isolated lobes via REST API
            code, h_res = http_req(
                base_url, "POST", "/api/lobes", {"name": "hack", "purpose": "Homelab tinkering"}, token=tok
            )
            assert code == 201 and h_res["name"] == "hack" and h_res["pushed"] is False

            code, p_res = http_req(
                base_url, "POST", "/api/lobes", {"name": "personal", "purpose": "Personal notes"}, token=tok
            )
            assert code == 201 and p_res["name"] == "personal"

            code, list_res = http_req(base_url, "GET", "/api/lobes", token=tok)
            assert code == 200 and [x["name"] for x in list_res["lobes"]] == ["hack", "personal"]

            # 3. Add raw source, check pending & sections, write page, read page
            code, raw_res = http_req(
                base_url,
                "POST",
                "/api/lobes/hack/raw",
                {"filename": "2026-10-30-pi5-notes.md", "content": "# Raw notes\n"},
                token=tok,
            )
            assert code == 201 and raw_res["committed"] is True

            code, pend = http_req(base_url, "GET", "/api/lobes/hack/pending", token=tok)
            assert code == 200 and "2026-10-30-pi5-notes.md" in pend["stdout"]

            code, secs = http_req(base_url, "GET", "/api/lobes/hack/sections", token=tok)
            assert code == 200 and "General" in secs["stdout"]

            code, wr = http_req(
                base_url,
                "PUT",
                "/api/lobes/hack/pages/pi5-setup",
                {"content": SAMPLE_PAGE, "section": "General", "description": "Pi 5 setup guide."},
                token=tok,
            )
            assert code == 200 and wr["created"] is True and wr["committed"] is True

            code, rd = http_req(base_url, "GET", "/api/lobes/hack/pages/pi5-setup", token=tok)
            assert code == 200 and rd["slug"] == "pi5-setup" and "sudo rpi-eeprom-config" in rd["content"]

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

            code, notif_resp = http_req(
                base_url, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=tok
            )
            assert code == 202

            code, tools_resp = http_req(
                base_url, "POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=tok
            )
            tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
            assert code == 200 and {"lobe_list", "lobe_sync", "lobe_search", "lobe_write_page", "lobe_lint"} <= tool_names

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

            # 8. MCP over stdio (--stdio subprocess)
            stdio_input = "\n".join([
                json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialize"}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
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
            assert len(lines) == 2
            assert lines[0]["id"] == 10 and lines[0]["result"]["serverInfo"]["name"] == "lobe-server"
            assert lines[1]["id"] == 11 and len(lines[1]["result"]["structuredContent"]["lobes"]) == 3
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
        "/api/lobes/{lobe}/remote:",
        "/api/lobes/{lobe}/sync:",
        "/api/lobes/{lobe}/search:",
        "/api/lobes/{lobe}/sections:",
        "/api/lobes/{lobe}/pending:",
        "/api/lobes/{lobe}/lint:",
        "/api/lobes/{lobe}/links/{page}:",
        "/api/lobes/{lobe}/raw:",
        "/api/lobes/{lobe}/pages/{slug}:",
        "/mcp:",
    ]:
        assert route in openapi, f"Missing route {route} in openapi.yaml"

    assert "COPY scripts/ /app/scripts/" in dockerfile
    assert "COPY templates/ /app/templates/" in dockerfile
    assert "COPY server/app.py /app/server/app.py" in dockerfile
    assert 'profiles: ["webui"]' in compose
    assert 'profiles: ["forgejo"]' in compose

    # Simulate exact Dockerfile layout in an isolated temp root (/app) and run MCP stdio E2E
    with tempfile.TemporaryDirectory() as tmp:
        app_root = Path(tmp) / "app"
        lobes_dir = Path(tmp) / "data" / "lobes"
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


if __name__ == "__main__":
    test_part1_core_and_git_scenarios()
    print("OK: Part 1 core & Git E2E scenarios passed")
    test_part2_http_rest_and_mcp_e2e()
    print("OK: Part 2 HTTP REST & MCP (HTTP + stdio) E2E scenarios passed")
    test_part3_container_isolation_and_compose()
    print("OK: Part 3 container isolation & compose verification passed")
