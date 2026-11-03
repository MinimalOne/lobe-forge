# Run lobe-server

`lobe-server` hosts all your lobes on one machine you control (a home server, Raspberry Pi, or VPS) and serves them over MCP and a REST API. Every AI tool you connect, from Claude Code and Codex on your laptop to Claude on your phone and a personal agent such as Hermes Agent, then shares the same second brain, with no wiki files on the devices themselves. It uses only the Python standard library.

Images for `linux/amd64` and `linux/arm64` are published to `ghcr.io/minimalone/lobe-forge` on every release, tagged `<X.Y.Z>`, `v<X.Y.Z>`, `<X.Y>`, and `latest`.

## 1. Start the server

Create an environment file, for example `/etc/lobes.env`, readable only by root (`chmod 600`):

```dotenv
# A long random secret, for example from: openssl rand -hex 32
LOBE_API_TOKEN=replace-with-a-long-random-secret
# Optional: Git access token, see "Push to a Git remote"
LOBE_GIT_TOKEN=replace-with-a-git-access-token
# Optional: push every change
LOBE_GIT_PUSH=1
```

Docker does not strip comments at the end of a line, so keep comments on their own lines. Without `LOBE_API_TOKEN` or `LOBE_OAUTH_ISSUER`, lobe-server refuses to start on a non-loopback address, which includes `0.0.0.0` inside the container.

Create `docker-compose.yml`:

```yaml
services:
  lobe-server:
    image: ghcr.io/minimalone/lobe-forge:latest   # or pin a version, for example :1.11
    container_name: lobe-server
    restart: unless-stopped
    env_file:
      - /etc/lobes.env
    ports:
      - "127.0.0.1:8080:8080"   # put a TLS reverse proxy in front for remote access
    volumes:
      - /srv/lobes:/data/lobes
```

```bash
sudo docker compose up -d     # sudo, because Compose reads the root-only env file
curl -s http://127.0.0.1:8080/health   # {"status": "ok"}
```

To reach lobe-server from other devices, expose it through a reverse proxy with TLS (nginx, Caddy, Traefik) at a name such as `https://lobes.example.com`.

The repository's [`server/docker-compose.yml`](../server/docker-compose.yml) also has optional profiles for a SilverBullet web editor on the same lobes (`--profile webui`, port 3001) and a Forgejo Git server (`--profile forgejo`, port 3002).

### Without Docker

```bash
LOBES_DIR=~/lobes LOBE_API_TOKEN=secret python3 server/app.py --host 127.0.0.1 --port 8080   # HTTP
LOBES_DIR=~/lobes python3 server/app.py --stdio                                              # MCP over stdio
```

## 2. Connect an assistant and create a lobe

Follow [clients.md](clients.md) for your assistant. Then ask it, for example:

> Create a `homelab` lobe on lobe-server for my server notes, push it to https://github.com/me/homelab.git, and install its skills.

The assistant calls `lobe_create`. lobe-server creates (or clones) `/data/lobes/homelab`, and returns four skill files that the assistant saves into your skills folder (for Claude Code, `~/.claude/skills/`). For an existing lobe, ask: *"Install the skills for the `homelab` lobe from lobe-server"* (`lobe_generate_skills`).

## 3. Push to a Git remote (optional)

Every change is committed in the lobe's local Git repository on the server. Pushing is optional: lobe-server pushes when the lobe has an `origin` remote and auto-push is on. A lobe's own `autopush` setting (`true` or `false`) wins; a lobe without one follows `LOBE_GIT_PUSH`.

For HTTPS remotes, set `LOBE_GIT_TOKEN`. lobe-server sends it as an HTTP Basic header and never writes it to `.git/config`. It sends the token only to one host, `LOBE_GIT_TOKEN_HOST` (default `github.com`), so a remote URL on any other host cannot receive it. For another Git host, set `LOBE_GIT_TOKEN_HOST` too, for example `LOBE_GIT_TOKEN_HOST=codeberg.org`, or `git.example.com:8443` for a server on a non-standard port. The username part is picked from the remote host:

| Git host | `LOBE_GIT_TOKEN` value | Minimum permissions |
| :--- | :--- | :--- |
| GitHub | fine-grained (`github_pat_...`) or classic (`ghp_...`) token | Fine-grained: **Contents: Read and write**, **Metadata: Read-only**, everything else **No access**. Classic: `repo`. |
| Codeberg, Forgejo, Gitea | token | repository: read and write |
| GitLab | token | `read_repository`, `write_repository` |
| Bitbucket Cloud | repository token, or `username:app_password` | Repositories: Read and Write |
| Any other HTTPS server | `username:password` | read and write |

For SSH remotes (`git@github.com:me/homelab.git`), leave `LOBE_GIT_TOKEN` unset and mount your SSH key and `known_hosts` read-only at `/root/.ssh` in the container.

When you set a remote, lobe-server first runs `git ls-remote` to check the repository exists and the credentials work. Each lobe keeps its settings in its own `.git/config` (`remote.origin.url`, `lobe.autopush`).

## 4. Enable OAuth for Claude.ai and the Claude mobile app (optional)

Claude.ai, Claude Desktop connectors, and the Claude mobile app sign in with OAuth instead of sending your `LOBE_API_TOKEN`. lobe-server acts as the OAuth *resource server*. Your identity provider (Authentik, Keycloak, or any provider with RFC 7662 token introspection) issues the tokens. `LOBE_API_TOKEN` keeps working for every other client.

1. In your identity provider, create a **confidential** OAuth client:
   - Redirect URI: `https://claude.ai/api/mcp/auth_callback`
   - Grant types: `authorization_code` and `refresh_token`
   - Scopes: `openid profile email offline_access` (`offline_access` gives Claude a refresh token, so you don't sign in again every hour)
   - PKCE with `S256` must be allowed.
2. Add to the environment file and restart:

   ```dotenv
   # The provider's issuer URL (this example is Authentik's format)
   LOBE_OAUTH_ISSUER=https://auth.example.com/application/o/lobe-server/
   LOBE_OAUTH_CLIENT_ID=<client id>
   LOBE_OAUTH_CLIENT_SECRET=<client secret>
   LOBE_PUBLIC_URL=https://lobes.example.com
   ```

3. Check that lobe-server advertises the issuer:

   ```bash
   curl -s https://lobes.example.com/.well-known/oauth-protected-resource
   ```

4. Add the connector in Claude.ai: see [clients.md](clients.md#claudeai-claude-desktop-and-the-claude-mobile-app).

lobe-server validates each token by calling the provider's introspection endpoint, caches valid tokens for up to 60 seconds, and rejects tokens issued to other clients. If the provider is unreachable, requests are rejected.

### Troubleshooting OAuth

| Symptom | Cause and fix |
| :--- | :--- |
| Claude.ai says "Authorization failed" and no login page appears | The client's allowed grant types do not include `authorization_code`. In Authentik, a provider created from the shell gets an empty `grant_types` list. |
| The connector connects, but every tool call fails with 401 | lobe-server cannot reach the issuer URL. If the issuer's name resolves to your public IP and your router has no hairpin NAT, add `extra_hosts: ["auth.example.com:host-gateway"]` to the lobe-server service so it reaches your reverse proxy directly. |
| You must sign in again every hour | The client is missing the `offline_access` scope, so Claude gets no refresh token. |

## 5. Turn on human page verification (optional)

A page counts as reviewed only when a person verifies it; an agent's token gets `403`. Give the person their own credential, in the environment file:

```dotenv
# simplest: a separate token you keep to yourself, never in an AI tool's config
LOBE_HUMAN_TOKEN=<long random token>
# or, with OAuth: a second client for people, or the lobe:verify scope on their tokens
LOBE_HUMAN_CLIENT_ID=<client id>
```

Then verify pages using `lobectl` from your terminal:

```bash
# Verify a page interactively (shows it in a pager, pins its content hash, asks on the terminal)
lobectl verify <lobe> <slug>

# Or view pages needing review (failing Check V)
lobectl verify list <lobe>
```

Alternatively, open `https://lobes.example.com/api/lobes/<lobe>/pages/<slug>/verify?token=<human token>` in a browser and click **Verify this page**. `wikictl lint` (or `lobectl lint <lobe>`) reports a verified page as "changed since review" (check V) once its content changes.

## Upgrade

```bash
sudo docker compose pull && sudo docker compose up -d
```

Start a new session in your assistant afterwards: MCP clients load the tool list once per session.

All settings: [reference.md](reference.md#environment-variables).
