# Quickstart - about 15 minutes, four principles, one command

This gets you the smallest useful piece of collab-os: **four principles** in a charter file and **one command, `/collab-os:gate`**, that asks Claude Code to stop and show you a decision card before it starts anything risky (the plugin cannot force it - see "Honest limits" in README.md).

This is for people who already use Claude Code, Anthropic's terminal assistant. If you do not have it yet, install it and sign in first - Claude Code's own documentation is at code.claude.com/docs - then come back here.

## Install with Claude Code (paste this)

Tested on macOS with Claude Code 2.1.296. On Linux, only the session-start hook and the tools were checked (installing Claude Code there wasn't tested). Windows and WSL haven't been tested. The session-start hook is a POSIX shell script, so on Windows it needs Git Bash (Git for Windows). Without Git Bash, Claude Code runs hooks through PowerShell, and this script won't run there.

**Easiest:** open Claude Code in the unzipped folder and type one line - `Read INSTALL.md in this folder and follow it step by step.` ([`INSTALL.md`](INSTALL.md) is the prompt below as a file, byte for byte; the pasted block is the path outside testers have used.)

Or paste it yourself: unzip the archive (or clone the repository), open Claude Code in that folder or the folder directly above it, and paste this whole block into the chat:

````text
Install the collab-os plugin for me from the folder I unzipped. Do the steps below in order and report the result of each step in one line.

Rules for this whole task:
- Write nothing outside: the collab-os folder, ~/.config/collab-os/, and what the `claude plugin` commands write on their own: ~/.claude/plugins/, the extraKnownMarketplaces and enabledPlugins entries in ~/.claude/settings.json, and Claude Code's own state file ~/.claude.json (plus its backup under ~/.claude/backups/). Do not edit Claude Code's own files (settings.json, .claude.json) yourself.
- No rm, no sudo, never overwrite an existing file, no network requests.
- If anything is unclear or a step fails, stop and ask me. Do not guess.

1. Find the absolute path of the unzipped collab-os folder: look in the current folder, its subfolders and its parent folders for plugin/.claude-plugin/marketplace.json, open each one you find and keep only those whose "name" is exactly collab-os. The folder that contains that plugin folder is the collab-os folder, whatever it is called - collab-os, collab-os-main (GitHub's Download ZIP) or a name your unzip tool shortened, such as collab-os-v0. The current folder may itself be that folder. If you do not find exactly one, ask me for the path and stop.
2. Run `claude plugin marketplace list`. If a marketplace named collab-os is already listed and points at this same folder, just continue. If it points somewhere else, show me both paths and ask whether to replace it; continue only if I say yes, by running `claude plugin marketplace remove collab-os` - tell me first that this also uninstalls the plugin that came from it and deletes its saved options, secrets and data. In either case, then run `claude plugin marketplace add <that absolute path>/plugin`, then `claude plugin install collab-os@collab-os`, then `claude plugin list`. If the list does not show collab-os@collab-os with Status: ✔ enabled, show me the output exactly as printed and stop.
3. If ~/.config/collab-os/CHARTER.md already exists, do not change it: ask me what to do and stop. Otherwise run `mkdir -p ~/.config/collab-os` and create ~/.config/collab-os/CHARTER.md containing exactly the text between BEGIN and END:
BEGIN
# My charter

## I2. Reality is the source of truth
The actual state beats documents, memory and guesses - meaning what the running
code path really reads. If you cannot check, do not assert.

## I3. No completion without verification
"Done" only after running and checking the real thing. Irreversible or structural
changes get a second pair of eyes that did not share the context that produced them.

## I4. Irreversible things belong to the human
Anything whose effect reaches people, money, live services or running systems
happens only after my approval. Without approval, the default is "don't".
Editing a file that a running process reads is a deployment.

## I8. Start from purpose
Decide what "finished" means before choosing a tool or a method. Purpose is the
reference point for the whole task: changing track is allowed, drifting without
knowing is not - a change of track is a declared decision. Finished means the
purpose is met, not that an artifact exists.
END
4. Tell me: "Quit this session and run claude again. In that new session ask: what did the collab-os hook tell you? The assistant should answer with the location of your charter."
5. Suggest one example task for trying the gate in that new session - for example `/collab-os:gate set up a nightly job that emails me a disk-usage report` - and stop. Do not carry out that task.
````

Everything this prompt does is the same as "Do it by hand" below - read it first, and if you would rather not hand it to the assistant, do it by hand.

One outside tester reported that v0.1.4 installed on a new Mac and reinstalled on an existing Mac without problems, using this paste-in prompt (step 1 remarked once that the folder name did not match - the wording above now allows other names; an earlier version stalled once at step 2; that is fixed). How long it takes has not been timed yet - if it stalls, tell us where.

You need: Claude Code, installed and **logged in** (run `claude` once and sign in before you start), and a terminal. git is only needed if you clone instead of unzipping. (Python 3.9+ is only needed later, for the optional tools.)

## The four principles

| # | Principle | In one line |
|---|---|---|
| I2 | Reality is the source of truth | Check the real thing before asserting anything; documents and memory are not evidence |
| I3 | No completion without verification | "Done" means it was run and checked, not that it was written |
| I4 | Irreversible things belong to the human | Anything that reaches people, money or running systems waits for your approval; without approval, the default is "don't" |
| I8 | Start from purpose | Decide what "finished" means first; changing track is a declared decision, drifting is not |

Everything else in this repository grows out of these four. The full set of ten is in `CHARTER-TEMPLATE.md`.

## Do it by hand

Steps 1-4 below do exactly what the pasted prompt does.

### Step 1 - get the files (1 minute)

Unzip the archive you received - it makes a folder named `collab-os` (GitHub's Download ZIP makes `collab-os-main`, and some unzip tools shorten the name, for example to `collab-os-v0` - use the name you see in the `cd` below). Or clone this repository into a folder of that name: copy the clone command from the green Code button on GitHub and add `collab-os` as the folder name at the end.

Then go into the folder:

```
cd collab-os
```

### Step 2 - install the plugin (2 minutes)

Run these from **inside** the `collab-os` folder:

```
claude plugin marketplace add ./plugin
claude plugin install collab-os@collab-os
```

(If your terminal is in the folder *above* it instead, the first command is `claude plugin marketplace add ./<folder name>/plugin` - for example `./collab-os/plugin` or `./collab-os-main/plugin`.)

`claude plugin list` should now show `collab-os@collab-os` with `Status: ✔ enabled`.

### Step 3 - write a four-article charter (2 minutes)

The plugin looks for your charter at `~/.config/collab-os/CHARTER.md` unless a config file says otherwise. Create it.

**The whole box below is one command: paste all of it into your terminal at once. You are not meant to copy the charter text into a file by hand.** If you already have a charter at that path, the command leaves it alone and says so.

```
if [ -e ~/.config/collab-os/CHARTER.md ]; then
  echo "CHARTER.md already exists - not touching it"
else
  mkdir -p ~/.config/collab-os && cat > ~/.config/collab-os/CHARTER.md <<'EOF'
# My charter

## I2. Reality is the source of truth
The actual state beats documents, memory and guesses - meaning what the running
code path really reads. If you cannot check, do not assert.

## I3. No completion without verification
"Done" only after running and checking the real thing. Irreversible or structural
changes get a second pair of eyes that did not share the context that produced them.

## I4. Irreversible things belong to the human
Anything whose effect reaches people, money, live services or running systems
happens only after my approval. Without approval, the default is "don't".
Editing a file that a running process reads is a deployment.

## I8. Start from purpose
Decide what "finished" means before choosing a tool or a method. Purpose is the
reference point for the whole task: changing track is allowed, drifting without
knowing is not - a change of track is a declared decision. Finished means the
purpose is met, not that an artifact exists.
EOF
fi
```

Want all ten articles instead? From inside the `collab-os` folder - if you have no charter yet: `mkdir -p ~/.config/collab-os && cp CHARTER-TEMPLATE.md ~/.config/collab-os/CHARTER.md`. If the starter charter is already there: `cp ~/.config/collab-os/CHARTER.md ~/.config/collab-os/CHARTER-starter-$(date +%Y%m%d-%H%M%S).md && cp CHARTER-TEMPLATE.md ~/.config/collab-os/CHARTER.md` - the old charter is copied to a time-stamped name first, so if the template is not found your charter stays as it was. Copies made in different seconds all stay; a rerun within the same second replaces that second's copy.

### Step 4 - open a new session (1 minute)

Start Claude Code in any project (quit the current session and run `claude` again). The session-start hook adds a short message to the session's context. You can ask "what did the collab-os hook tell you?" to see it. It contains lines like:

```
[collab-os] no config file - using built-in defaults
[collab-os] Charter location announced. Its text is NOT loaded yet - open it yourself when a rule decision comes up.
```

The first line appears only when you have no config file, which is normal for this quickstart.

If you see `Charter not found` instead, the file from Step 3 is not where the hook looked - the message prints the path it tried.

## You do not need the slash command most of the time

The three procedures are **skills**. Claude Code shows the model each skill's one-paragraph description, and the model is meant to call the skill itself when a task matches it - a new asset, a multi-step job, anything that touches something running. So in daily use you just ask for the work. If the task is risky, a decision card should appear before anything happens, without you typing a command.

`/collab-os:gate <task>` is the manual button. Use it to check that the gate works in your setup, or to force a card when you want one.

**Unmeasured:** how often the gate fires on its own outside the author's machine. Tester reports are being collected - cards issued without the command, risky tasks that went ahead without a card, and cards on tasks that did not need one. When there are numbers, this line changes. If you have tried it, tell us.

## How this differs from Claude Code's own safeguards

Claude Code has safeguards of its own: it asks before running a command, and one tester saw it stop a dangerous command right before it ran - some of this, that tester reports, still fires in auto-approve mode. Those safeguards work **per command, right before execution**. As the tester put it, deleting a local file is already caught. The gate is not for that.

The gate works **per task: once, before the work starts**, and asks "what does this touch, and how do we undo it?". In the tester's words, it is for work where **the command looks harmless but the result travels far**:

1. A one-line edit to a file that a running program reads - the edit is the deployment.
2. Registering a scheduled job - the effect arrives tomorrow, not now.
3. Anything that leaves the machine - mail, `git push`, a deploy.
4. Anything that costs money - API calls, paid services.

If a built-in safeguard already catches it, it is not a gate case.

## Step 5 - run `/collab-os:gate` on something real (3 minutes)

Pick a task that would touch something running. For example:

```
/collab-os:gate add a nightly job that emails me a disk-usage report
```

What should happen:

1. **Recon first** - Claude reports what it checked (existing jobs, existing scripts, how mail would be sent) and what it could not check.
2. **One card, five boxes** - purpose, output, horizon, where to do it, sources to load. Because this job would send mail on a schedule, expect a rollback line as well. The gate skill specifies these five boxes and their order; one tester (one person so far) said that was what they liked - the "things to decide" they usually get at the end of a task does not come as a table, the card is always the same table.
3. **It should stop:** no files created and nothing scheduled until you answer. If it does not stop, that is exactly the feedback we want.

That stop is the whole point. You get to say "yes", "no", or "not like that" *before* anything irreversible exists.

## You are done when

- [ ] `claude plugin list` shows `collab-os`
- [ ] a new session's hook message names your charter path
- [ ] `/collab-os:gate <task>` produced recon, a five-box card, and then stopped

## If something is off

| Symptom | Likely cause |
|---|---|
| `/collab-os:gate` does not autocomplete | The plugin is not installed or the session was opened before installing - start a new session |
| `Unknown command: /gate` | Type ``/collab-os:gate``. On v0.1.2 the short ``/gate`` did not work. |
| The installer asks you for the path | Drag the unzipped `collab-os` folder into the terminal window (it pastes the absolute path), or run `pwd` inside that folder, and paste the result |
| The installer says a charter already exists | Keep it and continue - the plugin uses the charter that is there. To start over, move the old file aside first and run the install again |
| `Charter not found` | The charter is not at `~/.config/collab-os/CHARTER.md`, or a config file points elsewhere. Check `COLLAB_OS_CONFIG` and `~/.config/collab-os/config.json` |
| `no config file - using built-in defaults` | Normal. A config file is optional; see `collab-os.config.example.json` when you want other paths |
| Claude starts working without a card | Say "that needed a gate" and record it in the gate log - a missed gate is exactly the kind of exception the log is for |

## Next

- `/collab-os:recon` and `/collab-os:handoff` - the other two procedures (`plugin/collab-os/README.md`)
- `ledger/LEDGER-TEMPLATE.md` - start recording the claims your assistant got wrong, by kind
- `tools/` - five small standard-library tools: `_config.py` (config lookup), `claim_scan.py` (a claim-scope thermometer), `rule_health.py` (dead pointers, numbering gaps, rule-count drift), `ledger_count.py` (counts the ledger, gate log and cases with the denominator) and `negative_control.py` (plants a known case before you report "0 found")

With the four-article charter from Step 3, `python3 tools/rule_health.py` (run inside the `collab-os` folder) reports that articles I1, I5, I6 and I7 are missing and not marked `(retired ...)`, plus two notes about a missing baseline and a missing rules file. That is expected: the numbers are kept from the full ten-article template on purpose, so a gap tells you which articles you have not adopted. Adding a line such as `## I1. (retired - not adopted yet)` for each missing number removes those findings; a clean run also needs a baseline that a person has approved (the tool explains how).

## Remove

```
claude plugin uninstall collab-os@collab-os
claude plugin marketplace remove collab-os
```

Run them in a terminal, from any folder. When they are done, `claude plugin list` should no longer show collab-os.

These two commands unregister the plugin and the marketplace. What stays behind: the downloaded copy under `~/.claude/plugins/cache/collab-os/`, Claude Code's own state file `~/.claude.json` with its backup, and the now-empty entries in `~/.claude/settings.json` - the measured list is in `plugin/collab-os/README.md`, "Remove". `~/.config/collab-os/CHARTER.md` is your own file and stays where it is - delete it yourself if you want it gone.
