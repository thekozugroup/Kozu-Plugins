# Kozu-Plugins

My plugin setup for Claude Code and Codex in one repo. Every machine that installs it gets the same plugins at the same versions, with the same defaults. Each machine keeps itself current from this repo.

| Plugin | What it does | Default here |
|---|---|---|
| [caveman](https://github.com/JuliusBrussee/caveman) | Compresses the agent's replies; keeps code, commands and errors exact | `ultracave` (caveman v4's name for "ultra") |
| [context-mode](https://github.com/mksglu/context-mode) | Runs large outputs in a sandbox and indexes them, so the context gets a summary instead of the dump | always on |
| [ponytail](https://github.com/DietrichGebert/ponytail) | Pushes the agent toward the smallest correct change | `full` |
| [TokenEconomizer](https://github.com/microsoft/TokenEconomizer) | Prunes tools a repo never uses and breaks failure loops; can learn repo facts | tool pruning and loop guard on, micro-skills off |

## Install on a machine

Needs `git`, Python 3.9+ (3.10+ for TokenEconomizer), Node 24.15+ (context-mode), and `claude` and/or `codex` on your PATH. macOS and Linux.

```bash
git clone https://github.com/thekozugroup/Kozu-Plugins.git ~/.kozu-plugins
```

```bash
~/.kozu-plugins/install.sh
```

This installs all four plugins into every host it finds, applies the defaults, and adds a job that runs `kozu sync` every 6 hours (a launchd agent on macOS, a crontab line on Linux). The job runs with the PATH you installed from, so it finds the same `claude`, `codex` and `node`.

Then, once per machine:

- **Codex:** start `codex`, open `/hooks`, and trust the plugin hooks. Codex asks again when an update changes a hook. That review is Codex's safety check, and kozu leaves it to you.
- **Claude Code:** nothing. Plugins load at the next session start.

If plugins were already installed from upstream GitHub, kozu moves them onto its pinned copies. In Claude Code it uninstalls with `--keep-data`, so plugin data survives the move.

## How updates flow

1. Every day, the [Track upstream releases](.github/workflows/track-upstream.yml) workflow checks each plugin's newest release tag (`vX.Y.Z`; pre-releases are skipped).
2. Before moving a pin, it checks out the new release and confirms the manifests kozu installs from are still there under the same names. A release that fails this check is not pinned, and the run fails so GitHub emails you.
3. It commits the new pins to `plugins.json` on the default branch, one commit per run, listing what moved.
4. Each machine's scheduled `kozu sync` pulls this repo, checks out the pinned commits under `vendor/`, and updates the hosts:
   - Claude Code reads each plugin directly from its checkout, so a new checkout is the update. It takes effect at the next session start.
   - Codex copies plugins into its cache, so kozu reinstalls a plugin whenever its pinned commit changes.

To roll a plugin back, revert the bump commit. Machines follow on their next sync.

## How the defaults reach threads and subagents

| | Claude Code | Codex |
|---|---|---|
| caveman | plugin hooks at session start and subagent start, mode from `~/.config/caveman/config.json` | caveman's own Codex installer: skills plus a session-start hook. Codex subagents get the rule from the managed block in `~/.codex/AGENTS.md` |
| ponytail | plugin hooks at session start and subagent start, mode from `~/.config/ponytail/config.json` | the same hooks, from the Codex plugin |
| context-mode | MCP server plus tool hooks; the hooks run on subagent tool calls too | MCP server plus tool hooks; the `AGENTS.md` block also tells subagents to use its tools |
| TokenEconomizer | hooks on every session and tool call | the same, from the Codex plugin |

The defaults live in [`profile.json`](profile.json) and are re-applied on every sync:

- `config_files`: keys merged into each plugin's config file. Other keys in those files are kept.
- `tokeneconomizer`: global settings, set with TokenEconomizer's own `te config set … --global`.
- `instruction_blocks`: a file whose text kozu keeps in a marked block of another file, here [`profile/codex-AGENTS.md`](profile/codex-AGENTS.md) into `~/.codex/AGENTS.md`. Text outside the markers is left alone.

These are defaults. Switching mode inside a session (`/caveman lite`, `/ponytail off`, `stop caveman`) still works for that session.

## Commands

| Command | Does |
|---|---|
| `kozu install` | `sync`, then `schedule` (`--no-schedule` to skip, `--hours N` to change the interval) |
| `kozu sync` | pull this repo, check out the pins, install or update each plugin, apply the profile. `--host claude` or `--host codex` for one host, `--force` to reinstall Codex plugins, `--no-pull` to use the local pins |
| `kozu status` | pin, checkout and per-host state for each plugin, plus version warnings |
| `kozu bump` | move pins to the newest upstream releases (what the workflow runs). `--dry-run` to only print |
| `kozu schedule` / `kozu unschedule` | add or remove the periodic sync |

Run it as `~/.kozu-plugins/kozu`, or put that folder on your PATH. Scheduled runs log to `~/.kozu-plugins/.state/sync.log`.

## Changing the setup

Edit and push; every machine picks it up on its next sync.

- **Change a default:** edit `profile.json`. For example, `"microskills": "on"` turns on TokenEconomizer's micro-skills, which make extra model calls.
- **Freeze a plugin:** set its `"track"` to `"pinned"` in `plugins.json`. The workflow then leaves its pin alone.
- **Follow a branch instead of releases:** set `"track"` to `"branch:main"`.
- **Add a plugin:** add an entry to `plugins.json` with its `repo`, the `claude` marketplace and plugin names from its `.claude-plugin/marketplace.json`, and for Codex either a `marketplace`/`plugin`/`path` (the folder holding its `.codex-plugin/plugin.json`) or an `installer` command run inside the checkout. Leave `"pin": {"ref": "none", "commit": ""}` and run `./kozu bump` to fill it.
- **Apply the same rules in Claude Code's own instructions:** add `"${CLAUDE_CONFIG_DIR}/CLAUDE.md": "profile/your-file.md"` to `instruction_blocks`. It is not set by default because the plugin hooks already cover Claude Code.

## Notes

- Update plugins with `kozu sync`, not with a plugin's own updater (`/ctx-upgrade`, `claude plugin update`, caveman's install script), or the next sync moves it back to the pin.
- If this repo is private, scheduled syncs need git credentials that work without a prompt: an SSH key or a credential helper. Install with `KOZU_REPO=git@github.com:thekozugroup/Kozu-Plugins.git` to clone over SSH.
- The workflow pushes to the default branch with the built-in `GITHUB_TOKEN`. If your organization limits workflows to read-only access, allow write access under Settings → Actions → General → Workflow permissions.
- Ponytail's task board (the "band") has settings Claude Code reports as unset after install. They are optional; set them with `/plugin configure ponytail@ponytail`.
- Windows is not supported: the scheduler and Codex marketplace links assume macOS or Linux.
- To remove everything: `kozu unschedule`; uninstall the plugins with `claude plugin uninstall` and `codex plugin remove`; remove caveman's Codex hook with `npx -y github:JuliusBrussee/caveman -- --uninstall`; then delete `~/.kozu-plugins`.

## Tests

```bash
python3 -m unittest discover -s tests
```

They run against local git repos and fake `claude`/`codex` CLIs, with no network.
