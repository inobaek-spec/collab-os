---
name: recon
description: Recon before you act - before creating an asset, moving, renaming or editing a file that a running process reads, asserting that something does or does not exist, or designing from documents and memory alone, check the real thing first and put the result at the top of your reply. Use before creating files, changing a file that something running reads, asserting existence or absence, or designing from documentation alone.
---

# Recon before you act

Charter article I2: reality is the source of truth - meaning *what the running code path actually reads*, not the file that looks right. This skill holds the procedure; the rule lives in the charter the session-start hook announced.

## Invoked by hand

`/collab-os:recon <target>` runs this procedure now.

Target: $ARGUMENTS

Check the real thing (the target's files and folders, recent history such as `git log`, modification times, and whatever is actually running that reads it) and **report first what you checked and what you found**. If you were called by hand (the user's message was the slash command) and the target above is empty, ask what to look at. If you invoked this skill yourself while working (the target may then arrive empty), the thing you were about to create, change or assert is the target - do not ask back; do the recon. Claim that something is absent only on the strength of a lookup, and say how far the lookup went. If documents and reality disagree, reality wins - say so explicitly.

## Triggers - if any one applies, do not proceed without recon

1. Creating a new asset
2. Moving, renaming or editing a file that a running process reads (a small, reversible edit to a file nothing running reads is a chore - see the launch gate)
3. Asserting "it does not exist" or "it already exists"
4. Designing from documents or memory alone
5. Making a claim about how another system is built

## Rules

- When a trigger fires, **show the recon result first in your reply** - what you checked and what came back - and only then do the work.
- An assertion, creation or move made without showing recon is itself a violation. When you notice one: stop, do the recon, continue, and record the slip in the claim-error ledger.
- **Absence is claimed only from a lookup**, and the claim says how far the lookup went.
  Weak: "the front end never reads the URL". Strong: "inside `index.html` there are 0 places that read the URL". The strong form can be refuted in one line by the next person, which is the point.
- If documents or memory disagree with reality, **reality wins**: say so, update the note, and put an observation date on anything you record about structure.
- If something already exists for the same purpose, revise it instead of creating a second one.
- When the recon leads to a proposal, a decision or a change, end the recon result with the same one line the gate card carries: `Rules checked: <paths> · overlap/conflict: <what, or "none found in" the files listed>` - the rule files that govern the target (the charter and the always-loaded rules file the hook announced, and the target's own policy `.md`). The gate card and the recon result share this line so the reader sees it in the same shape every time. It covers `.md` rules, not code.

## Basic recon set

| Target | Check |
|---|---|
| Map | The project's own index or README, if it has one, and whatever health or status notes it keeps |
| The thing itself | Its folder tree, recent history (`git log`), modification times, and the tests that cover it |
| What is running | Any scheduler, service or job that reads the target, and when it runs next - editing a file a running job reads is a deployment |
| Rules | The rule files that govern the target - the charter, the always-loaded rules file, the target's own policy `.md`. Which ones you read, and whether what you are about to do overlaps or conflicts with them; "none" names the files checked |

Adapt the set to your machine. The rule is not the list; the rule is "look before you assert, and show what you looked at".

## Marking

Write facts you observed as `[observed YYYY-MM-DD]` so they stay distinct from what someone told you. Only things the human said are marked `[stated]`.
