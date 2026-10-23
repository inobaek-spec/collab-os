# collab-os plugin v0.1.4

Three procedures and one session-start hook for Claude Code. The plugin holds **procedures only**; the rules they enforce live in your own charter file (start from `CHARTER-TEMPLATE.md` at the repository root).

## What it solves

Working rules for an AI assistant tend to get pasted into several places - a global rules file, a project file, a memory note - and the copies drift apart. This plugin replaces the procedure copies with **pointers to one charter you own**, and lets you call each procedure by hand as `/collab-os:<name>`.

## What it does not solve (honest limits)

- **Claude Code only.** Other surfaces (web chat, project spaces, other models) do not load plugins. Give them the charter some other way.
- **Nothing is enforced.** The hook only *announces* where the charter is; it does not load the charter text and it never blocks a tool call. The model still has to open the charter and follow it.
- **No verification of your charter.** If your charter is wrong, the plugin points faithfully at the wrong thing.

## Contents

| Kind | Name | What it does |
|---|---|---|
| skill | `gate` | The 5-box decision card procedure, plus when the gate fires |
| skill | `recon` | Check reality before asserting, creating or moving anything; show the result first |
| skill | `handoff` | The 7-section handoff block for surface or model switches |
| by hand | `/collab-os:gate <task>` `/collab-os:recon <target>` `/collab-os:handoff <destination>` | The same three skills, called directly. What follows the name is passed to the skill as its target |
| hook | `SessionStart` | Prints the charter path, gate log, claim log and handoff folder into the session |

## Design choices

- **No rule text in the plugin.** Skills reference charter articles by number (I1, I2, I4, I5, I8). Change a rule in one place - the charter.
- **Non-blocking.** Only `SessionStart` is used. A blocking `PreToolUse` hook was left out on purpose: a rule should prove itself in practice before it becomes a hard block.
- **Fail-open.** The hook always exits 0. A bug in the gate must not stop a session. If the config or the charter is missing, the first line of the hook message says so.
- **Paths come from config, not code.** The hook looks for a config file in this order: the file named by `COLLAB_OS_CONFIG`, then `~/.config/collab-os/config.json`, then built-in defaults under `~/.config/collab-os/`. See `collab-os.config.example.json` and `tools/_config.py` at the repository root.

## Install

From inside the unzipped or cloned `collab-os` folder:

```
claude plugin marketplace add ./plugin
claude plugin install collab-os@collab-os
```

(From the folder *above* `collab-os`, the first command is `claude plugin marketplace add ./collab-os/plugin`.)

Check: `claude plugin list` shows `collab-os`; in a new session, `/collab-os:gate` autocompletes and the session-start message appears.

## Remove

```
claude plugin uninstall collab-os@collab-os
claude plugin marketplace remove collab-os
```

Run them in a terminal, from any folder. When they are done, `claude plugin list` should no longer show collab-os.

`claude plugin uninstall collab-os` (short name) also works - both forms were checked on Claude Code 2.1.296.

What these two commands undo, checked on Claude Code 2.1.296 in a clean home folder: the plugin is unregistered (`enabledPlugins`, `installed_plugins.json`) and the marketplace entry is removed (`extraKnownMarketplaces`, `known_marketplaces.json`). What stays: the downloaded copy under `~/.claude/plugins/cache/collab-os/` (Claude Code marks it orphaned instead of deleting it), Claude Code's own state file `~/.claude.json` with its backup, and the now-empty entries in `~/.claude/settings.json`. Delete the cache folder yourself if you want it gone. Your charter and logs are your own files and are never touched.
