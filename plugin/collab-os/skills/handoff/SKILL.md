---
name: handoff
description: Handoff block for surface or model switches - when work moves between chat, a project space, a desktop agent, Claude Code, or to a different AI model, print the purpose, current state, settled decisions, next steps and file paths first so context does not fork. Use when switching surfaces, handing work to another window, session or model, or when a conversation should continue somewhere else.
---

# Handoff block

Charter article I5: the human's understanding is the bottleneck - **there are no silent decisions**. When work changes hands, print the handoff block *first*. Purpose travels before context.

## Invoked by hand

`/collab-os:handoff <destination surface or topic>` writes the block now.

Destination surface / topic: $ARGUMENTS

Fill all seven sections (purpose, current state, settled decisions, next steps, file paths, verification criteria, open items) and put the one-line resume sentence on top. If you were called by hand (the user's message was the slash command) and the destination above is empty, ask where the work is going. If you invoked this skill yourself while working (the destination may then arrive empty), the switch you were about to make is the destination - do not ask back; write the block. Do not leave the block only in the conversation: save it as `HANDOFF-<YYYY-MM-DD>-<topic>.md` in the handoff folder the session-start hook announced.

## Format (seven sections, fixed order)

```
Resume: "Read <absolute path>/HANDOFF-<date>-<topic>.md and continue from 'Next steps'."

## Handoff - <title> (<date>, <from> -> <to>)

## Purpose
One line. What has to be true for this to be finished?

## Current state
What is done / what is not

## Settled decisions
Numbered. Anything that would need a human decision to undo

## Next steps
In a form the receiving side can start on immediately

## File paths
Sources of truth, targets, things to verify

## Verification criteria
What do we look at to decide it landed?

## Open items
What was not touched, and why (including anything held back because it is hard to undo)
```

## First duty of the receiving side - recon the premises before executing "Next steps"

**"Next steps" is the sender's guess, not the receiver's fact.** Before starting, check each step's premise against reality and put the result first in your reply (same rule as the `recon` skill).

| If a next step says... | Check this first |
|---|---|
| "Create X" | Does something with the same purpose already exist? If yes, this is a revision, not a creation |
| "X is missing / X does not work" | Look for yourself. Absence backed only by a document or memory is not evidence |
| "X is already done" | Check that too. "Done" can be wrong |
| It contains numbers or counts | **Read them as reference values and measure again** - a block starts going stale the moment it is written |

Why: a handoff block can name, as next steps, two things that were already solved - and a single directory listing would have shown it. **Recon resets when the surface changes**: what the sender checked is not what the receiver has checked.

## When to switch

| Switch | When |
|---|---|
| Chat -> project space | The same topic for the third time, the same material attached twice, or the result will be needed again |
| Chat -> agent with file access | Three or more files to create or edit, local files needed, ten or more tool calls expected, or anyone is about to copy-paste between windows |
| Project -> agent | Design is done and execution starts |
| Agent -> chat | Only judgement and discussion are left |
| One AI model -> another | You are near a usage limit, or what remains is mostly execution rather than judgement. A person carries this one across - there is no bridge |

**Hold back** when five or fewer turns of work remain: finish where you are.

## Handing off to a different model - extra rules

Unlike a surface switch, **no tools, rules or memory travel with you.** The only channel is one file, carried by a person, so the block must stand on its own.

- **All paths absolute.** Do not assume the other side has the same working directory. Quote paths that contain spaces or non-ASCII characters.
- **Do not use your own tool or skill names as instructions.** "See the recon skill" means nothing on the other side - write the action out as a sentence. The same goes for slash commands, sub-agents and hooks.
- **Rules travel as pointers.** Point at the source-of-truth file; do not paste a new copy of the rules. Copies drift.
- **Put the one-line resume sentence on top.** Acceptance test: given only that one line, the other side resumes without asking questions.
- **Do not hand over anything hard to undo.** Sending, deploying, deleting and changing running jobs stay in "Open items" - the other side has none of your gates.
- **Hand back with a block too.** Work done over there that is not recorded here gets re-discovered by the next session.

## Save it as a file

A handoff block does not stay only in the conversation (charter I1: outputs are files when they feed the next piece of work). Save it as `HANDOFF-<YYYY-MM-DD>-<topic>.md` in the handoff folder the session-start hook announced.
