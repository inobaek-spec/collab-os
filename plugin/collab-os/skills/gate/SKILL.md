---
name: gate
description: Launch gate - for work where the command looks harmless but the result travels far. Before starting such work, present one 5-box decision card (what it touches, how to undo it) and wait for the human's confirmation. Reaches far means - a file that a running program reads, a scheduled job, anything that leaves the machine (mail, push, deploy), anything that costs money, a new asset, or multi-step or recurring work. Not for what Claude Code's own safeguards already stop per command, such as deleting a local file. Use when starting a task whose result reaches beyond the files in front of you, or when unsure how far it reaches.
when_to_use: Registering a scheduled job (cron, launchd, a timer) · sending mail or a Slack message · git push, a deploy, a release · editing a config or data file that a running program reads · setting up repeated or paid API calls · creating a new asset, or starting multi-step or recurring work · any request where you are unsure how far the result reaches.
---

# Launch gate

**The rules are not in this file.** They live in the charter whose path the session-start hook announced (articles I8 "start from purpose" and I4 "irreversible things belong to the human"). This skill holds the procedure only. If this file and the charter disagree, the charter wins. If no charter was found, write "charter not loaded" in box 5 and issue the reduced card anyway.

## Invoked by hand

`/collab-os:gate <what you are about to do>` runs this procedure now.

Target: $ARGUMENTS

Keep the order: (1) show the recon result, (2) the 5-box card, (3) **stop and wait for the human's confirmation**. Do not start the real work without a card. If you were called by hand (the user's message was the slash command) and the target above is empty, ask what the task is before issuing the card. If you invoked this skill yourself while working (the target may then arrive empty), the task you were about to start is the target - do not ask back; go straight to recon and the card. The rules themselves live in the charter whose path the session-start hook announced; if no charter was found, say so in box 5 and issue the reduced card.

## Does the gate fire?

| Pass (no card) | Fire (card needed) |
|---|---|
| A single question | Creating a new asset (file, script, job, document set) |
| A small, reversible edit to one file | Multi-step, recurring or project-sized work |
| | **Anything that reaches a running system, people, money or the outside world - regardless of file count** |

When unsure, issue the card. "It's only one file" is not an exemption: one edit to a file that a running job reads is a deployment.

Installing a hook, registering a scheduled job, or writing into a repository you do not own all count as "reaching a running system".

## Procedure

1. **Recon first** - follow the `recon` skill and put its result *before* the card.
2. **Present one card** (five boxes plus the Rules checked line, below).
3. **Stop.** Do not move to the real work before the human confirms.
4. Where a fork needs a human decision, lay out the material and the options; do not pick one and carry on.

## Card format

| Box | What goes in |
|---|---|
| 1. Purpose | One line. **The finish line is the purpose, not the artifact.** |
| 2. Output | A file, a change to a system, or a decision - which one? |
| 3. Horizon | One-off, recurring, or project; and how far ahead it reaches |
| 4. Where to do it | A one-line verdict on which surface or tool should do this, plus a request to confirm (a verdict, not a questionnaire) |
| 5. Sources to load | The files to read before starting (the charter, the target, its tests) |

The gate card is always this table as it stands: do not rename the boxes or change their order. Two lines follow the table, in this order, never inside a box:

1. **Rules checked** - always. The rule files you read for this task, by path, and whether the planned change overlaps or conflicts with any of them: the charter the hook announced, the always-loaded rules file the hook announced, and the target's own policy `.md` files (a `CLAUDE.md` or `AGENTS.md` next to the target, a README's rules section). Write it as `Rules checked: <paths> · overlap/conflict: <what, or "none found in" the files listed>`. A bare "none" is not an answer: "none" names the files it covers, exactly as an absence claim in recon says how far the lookup went. Code is not in scope of this line - say so if asked. If the hook found no charter, `charter not loaded` is the first entry.
2. **How to roll it back** - whenever the work is hard to undo. It goes directly under the Rules checked line.

Why the first line is always there: a reader who wonders "did you check this against my existing rules?" should not have to ask (tester feedback, 2026-10-26). The line appears when the gate or the recon fires; a plain chore has neither.

## Choosing where to do it (box 4)

1. **Ownership first** - if a session, window or tool already owns that asset, the work goes there, even if another tool looks more convenient.
2. If the output is files or system changes, use a surface that can actually reach them and run the checks.
3. If the same topic keeps coming back with the same reference material, use a persistent project space.
4. Everything else can stay in plain chat.

## Ordering several changes

When a task contains several changes, order them by four questions, in this order:
reversibility (reversible first) -> how fast the information goes stale -> how much each one blocks the others -> **contact with running things (the change that touches nothing running goes first)**.
Extra rule: if an independent reviewer is about to run, do not change what that reviewer actually reads just before it runs.

## Recording exceptions

If the gate failed to fire, fired when it should not have, or reached the wrong verdict, add one row to the gate log whose path the session-start hook announced. Normal, correct gate runs are not logged - only exceptions.

If that file does not exist yet, start it from `ledger/GATE-LOG-TEMPLATE.md` in the collab-os repository. Put the exception type (`missed`, `over-fired` or `misjudged`) as the first word of the third column so the rows can be counted.
