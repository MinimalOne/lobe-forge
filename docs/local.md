# Local lobes

Local lobes live on your own computer, in `~/lobes/<name>`, and never leave it unless you push them to a Git remote.

- **Create and manage** them with the `/lobe-forge` Claude Code plugin (this page).
- **Use** them from Claude Code through the plugin's skills, or from any other MCP tool (Codex, Gemini CLI, Antigravity, Cursor, Hermes Agent, and others) by running lobe-server over stdio: see [Local lobes without a server](clients.md#local-lobes-without-a-server).
- **Reach them from other devices or your phone** by moving them to a [self-hosted lobe-server](server.md).

**What you need:** Claude Code, Git, and Python 3.10 or newer. Install the plugin once:

```bash
claude plugin marketplace add MinimalOne/lobe-forge
claude plugin install lobe-forge@lobe-forge
```

You normally just talk to the plugin: `/lobe-forge` asks for what it needs and runs `scripts/forge.py` for you. The `forge.py` commands below are for automation and troubleshooting. The script is in this repository's `scripts/` folder, or in `~/.claude/plugins/cache/lobe-forge/lobe-forge/<version>/scripts/`.

## Common tasks

| Task | Say to Claude Code |
| :--- | :--- |
| Create a lobe | `/lobe-forge create a family lobe for school, health, and holidays` |
| Find existing wikis in a folder | `/lobe-forge scan ~/code` |
| Adopt existing wikis as lobes | `/lobe-forge adopt the wikis in ~/code` |
| Install a cloned lobe on a new machine | `/lobe-forge install the lobe I cloned to ~/lobes/personal` |
| Upgrade lobes to the current templates | `/lobe-forge upgrade my lobes` |
| Remove a lobe's skills | `/lobe-forge uninstall the personal lobe` |

## Create a lobe

1. Run `/lobe-forge`.
2. Enter the lobe **name** (for example `personal`), its **purpose**, the **repository path** (default `~/lobes/<name>`), and which **Claude installations** get the skills.
3. Restart Claude Code. You can now ask in plain words (*"save this to homelab"*, *"ask homelab how backups work"*), or use the lobe's slash commands `/<name>-add-raw`, `/<name>-ingest`, `/<name>-ask`, and `/<name>-lint`.

Each skill is a symbolic link (a shortcut) from your Claude configuration directory (for example `~/.claude/skills/personal-ask`) to the lobe repository. Every ingest creates a Git commit.

## Adopt existing wikis

`/lobe-forge` can scan a repository, or a folder of repositories, for `llmwiki/` or `wiki/` folders that contain an `index.md` file, and adopt each one as a named lobe. It keeps the existing folder structure and does not rewrite existing pages.

```bash
python3 scripts/forge.py scan ~/code      # Read-only report
```

The report shows whether Git tracks each wiki folder, how many authors commit to its raw source folder, and which installed skills or plugins already use it.

There are two ways to adopt:

- **In place**, for repositories you own: the lobe files are written into the repository and committed.
- **External**, for third-party or team repositories: the lobe files go into `~/lobes/_adopted/<name>/` with a `repo` symbolic link to the target repository. The target repository is never modified. The generated skills ask before every commit and never push.

## Install a lobe on another machine

Clone the lobe repository, then run:

```bash
python3 scripts/forge.py install --repo <lobe-repo> --target ~/.claude
```

For an external lobe, run `create` again with the same `<name>`, `--repo`, `--wiki-dir`, `--raw-dir`, and `--home` arguments to rebuild its home folder.

## Upgrade lobes

`create` never overwrites existing files. After you update lobe-forge, run `upgrade` to refresh a lobe's generated files (`wiki_tool.py`, `wikictl`, `LOBE.md`, and `skills/<name>-*/SKILL.md`):

```bash
python3 scripts/forge.py upgrade --repo <lobe-repo-or-home>           # Dry run: show changes
python3 scripts/forge.py upgrade --repo <lobe-repo-or-home> --apply   # Apply changes
python3 scripts/forge.py upgrade --all [--apply]                      # All installed lobes
```

- `--all` finds lobes from the skill links in your Claude configuration directories.
- `upgrade` does not touch custom skills, edited skills that no longer call `wikictl`, wiki pages, or raw sources.
- For in-place lobes, commit pending changes before `--apply`. For external lobes, `--apply` writes `*.bak` backups.

## Uninstall or move a lobe

```bash
python3 scripts/forge.py uninstall --repo <lobe>              # Remove skill links
python3 scripts/forge.py uninstall --repo <home> --purge      # External lobe: also delete its home folder
```

To move an external lobe into its own repository:

1. Copy the wiki folder into the new repository.
2. Run `uninstall --purge` on the old home folder.
3. Run `create` in the new repository with `--origin "<where it came from>"`.

## Origin and publishing

`LOBE.md` records where a lobe came from: `new`, the adopted repository's remote URL, or the `--origin` value. `/lobe-forge` offers to publish a lobe to your GitHub account only when you own the content, and creates the repository as private.

## Move local lobes to lobe-server

Your local lobes are plain Git repositories, so lobe-server can clone them. Push the lobe to a Git remote, then ask your assistant to create it on lobe-server with that `remote_url`. See [server.md](server.md) and [how-it-works.md](how-it-works.md#using-local-skills-and-lobe-server-on-the-same-lobe).
