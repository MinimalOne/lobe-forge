# Reference

## Environment variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOBES_DIR` | `/data/lobes` | Folder that holds all lobe repositories. |
| `LOBE_HOST` | `0.0.0.0` | HTTP bind address (`--host` overrides it). |
| `LOBE_PORT` | `8080` | HTTP port (`--port` overrides it). |
| `LOBE_API_TOKEN` | *(unset)* | Bearer token for `/api/*` and `/mcp`. If both this and `LOBE_OAUTH_ISSUER` are unset, authentication is off. `/health` is always public. |
| `LOBE_OAUTH_ISSUER` | *(unset)* | Issuer URL of your OAuth provider, for example `https://auth.example.com/application/o/lobe-server/`. Turns on OAuth for Claude.ai and the Claude mobile app. `LOBE_API_TOKEN` keeps working. |
| `LOBE_OAUTH_CLIENT_ID` | *(unset)* | OAuth client ID. Used for token introspection; tokens issued to other clients are rejected. |
| `LOBE_OAUTH_CLIENT_SECRET` | *(unset)* | OAuth client secret for token introspection. |
| `LOBE_PUBLIC_URL` | *(unset)* | Public base URL, for example `https://lobes.example.com`. Used in `/.well-known/oauth-protected-resource` and the `WWW-Authenticate` header. |
| `LOBE_GIT_PUSH` | `0` | Push every change (`1`, `true`, or `yes`) for lobes that don't set `lobe.autopush`. |
| `LOBE_GIT_TOKEN` | *(unset)* | Token or `username:token` for HTTPS Git (`clone`, `fetch`, `pull`, `push`, `ls-remote`). See [server.md](server.md#3-push-to-a-git-remote-optional). |
| `LOBE_SERVER_VERSION` | *(from `plugin.json`)* | Overrides the version reported by MCP `initialize`. |

## MCP tools

lobe-server implements MCP protocol version `2025-06-18` over Streamable HTTP (`POST /mcp`) and stdio (`--stdio`).

| Tool | Required | Optional | Description |
| :--- | :--- | :--- | :--- |
| `lobe_list` | | | List all lobes on the server. |
| `lobe_create` | `name`, `purpose` | `remote_url`, `autopush`, `generate_skills`, `skill_mode` | Create a lobe, or clone an existing remote repository. With `generate_skills=true`, also return its four skill files. |
| `lobe_generate_skills` | `lobe` | `mode` (`mcp` or `local`) | Return the four `<name>-*/SKILL.md` files for an existing lobe. |
| `lobe_configure_remote` | `lobe` | `remote_url`, `autopush` | Set the Git `origin` URL and auto-push. |
| `lobe_sync` | `lobe` | `push` | Fetch and rebase from `origin`, then push if enabled. |
| `lobe_search` | `lobe`, `terms` | `no_decay` | Search pages. `terms` is a list of strings; use `a\|b` for alternatives. |
| `lobe_links` | `lobe`, `page` | | Incoming and outgoing `[[wiki-links]]` of a page. |
| `lobe_sections` | `lobe` | | `## Section` headings in `wiki/index.md`. |
| `lobe_pending` | `lobe` | | Sources in `raw/` that no page cites yet. |
| `lobe_lint` | `lobe` | `strict` | Check frontmatter, links, orphans, and index drift. |
| `lobe_add_raw` | `lobe`, `filename`, `content` | | Save a source in `raw/`, commit, and push if enabled. |
| `lobe_read_raw` | `lobe`, `filename` | | Read `raw/<filename>`. |
| `lobe_read_page` | `lobe`, `slug` | | Read `wiki/<slug>.md`. |
| `lobe_write_page` | `lobe`, `slug`, `content` | `section`, `description`, `log_entry` | Write `wiki/<slug>.md`, optionally add it to `index.md` and append to `log.md`, commit, and push if enabled. |

## REST API

The full specification is in [`openapi.yaml`](../openapi.yaml) (OpenAPI 3.1). Send `Authorization: Bearer <LOBE_API_TOKEN>` with every request to `/api/*` and `/mcp`.

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Public health check: `{"status": "ok"}`. |
| `GET` | `/.well-known/oauth-protected-resource` | OAuth metadata (only when `LOBE_OAUTH_ISSUER` is set). Public. |
| `GET` | `/api/lobes` | List lobes with remote URL and auto-push status. |
| `POST` | `/api/lobes` | Create or clone a lobe. Body: `{"name", "purpose", "remote_url?", "autopush?", "generate_skills?", "skill_mode?"}`. |
| `GET` | `/api/lobes/{lobe}/skills?mode=mcp` | Generate the four skill files (`mode=mcp` or `mode=local`). |
| `PUT` | `/api/lobes/{lobe}/remote` | Set `origin` and auto-push. Body: `{"remote_url?", "autopush?"}`. |
| `POST` | `/api/lobes/{lobe}/sync` | Fetch, rebase, and optionally push. Body: `{"push?"}`. Returns `409` on a rebase conflict. |
| `GET` | `/api/lobes/{lobe}/search?q=a\|b&limit=5` | Search pages. Repeat `q` for several terms. |
| `GET` | `/api/lobes/{lobe}/links/{page}` | Incoming and outgoing links of a page. |
| `GET` | `/api/lobes/{lobe}/sections` | `## Section` headings in `wiki/index.md`. |
| `GET` | `/api/lobes/{lobe}/pending` | Sources in `raw/` that no page cites. |
| `GET` | `/api/lobes/{lobe}/lint?strict=true` | Wiki health check (`returncode: 0` when clean). |
| `POST` | `/api/lobes/{lobe}/raw` | Save a source and commit. Body: `{"filename", "content"}`. |
| `GET` | `/api/lobes/{lobe}/raw/{filename}` | Read a source. |
| `GET` | `/api/lobes/{lobe}/pages/{slug}` | Read a page. |
| `PUT` | `/api/lobes/{lobe}/pages/{slug}` | Write a page; optional `section` + `description` add it to the index, `log_entry` appends to the log. |
| `POST` | `/mcp` | MCP JSON-RPC 2.0 endpoint (Streamable HTTP). |

### Examples

```bash
# Create or clone a lobe with auto-push
curl -s -X POST https://lobes.example.com/api/lobes \
  -H "Authorization: Bearer $LOBE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "homelab", "purpose": "Home server notes",
       "remote_url": "https://github.com/me/homelab.git", "autopush": true}'

# Search
curl -s "https://lobes.example.com/api/lobes/homelab/search?q=nginx|proxy&limit=5" \
  -H "Authorization: Bearer $LOBE_API_TOKEN"

# Pull, rebase, and push
curl -s -X POST https://lobes.example.com/api/lobes/homelab/sync \
  -H "Authorization: Bearer $LOBE_API_TOKEN" -H "Content-Type: application/json" \
  -d '{"push": true}'
```
