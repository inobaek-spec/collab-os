# Changelog

## v0.1.5 - 2026-11-02

### Changed
- Gate card: a `Rules checked:` line now always follows the five-box table, before the rollback line - which rule files (`.md`) were read for the task and whether the change overlaps or conflicts with them; "none" names the files it covers. Tester feedback 2026-10-26.
- Recon: a recon result that leads to a proposal or a change ends with the same line.
- Session-start hook: announces the always-loaded rules file (`rules_file`, default `~/.claude/CLAUDE.md`) so the line can name it.
- Install prompt, step 3: finds the active charter the way the plugin does (`COLLAB_OS_CONFIG` → `~/.config/collab-os/config.json` → default; a non-empty `COLLAB_OS_CONFIG` naming a missing or broken file means the default path, not config.json). If a charter is already there, it shows the missing starter articles and appends them to the end **only after you say yes**, after a backup `<charter>.bak-<YYYYMMDD-HHMMSS>`. The ten-article swap command now uses the same backup name.
- Tested-environment note: Claude Code 2.1.296 (v0.1.4, outside tester) and 2.1.316 (v0.1.5 probes).

### Not measured / limits
- Probes (2026-11-02, Claude Code 2.1.316, model sonnet, one run per case, `claude -p` inside a macOS `sandbox-exec` profile that denies every file write except the probe folder and Claude Code's own temp folders): the `Rules checked` line appeared as one line under the card in 2/2 gate runs and 1/1 recon run, and was absent with the v0.1.4 skills (2/2); step 3 found the charter through `COLLAB_OS_CONFIG`, listed the missing article and stopped without writing in 3/3 "ask" cases, treated a blanket "yes to everything" as not an answer (1/1), and after an explicit yes made a byte-identical backup and appended the article to the end (1/1). Not measured: the config.json fallback, empty or broken config branches, auto-firing, other models, and whether the files named in the line were really read.
- In the yes branch Claude Code itself asks once more before the shell command that appends; the probe ran with that prompt disabled.
- The `Rules checked` line appears when the gate or the recon fires; a plain chore (a small reversible edit nothing running reads) has neither.
- Whether the line is *correct* - that the files were really read - is not measured; a wrong line is a `misjudged` row in the gate log.
- The session-start hook reads the config file with a regex (plain `"key": "value"` pairs only), so it can still announce a charter path from a config file that Python and the install prompt reject as broken JSON; the two then disagree until the file is fixed (known difference, see `tools/_config.py` limit C4).

### Rollback
- Reverting or reinstalling the plugin never touches your charter. If step 3 appended articles, the `.bak-…` copy it made first is the way back: `cp "<backup>" "<charter>"`.

## v0.1.4 - 2026-11-01
- First public draft. Install prompt tolerant of folder names; `INSTALL.md` / `INSTALL.ko.md` one-line path; README wording pass. (One outside tester: installed on a new Mac and reinstalled on an existing one.)
