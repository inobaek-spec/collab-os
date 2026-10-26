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
