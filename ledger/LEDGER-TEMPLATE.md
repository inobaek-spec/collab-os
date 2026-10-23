# Claim-error ledger (template)

> Copy this file to the path your config names as `claim_log` (default `~/.config/collab-os/CLAIM-LOG.md`) and start appending entries at the bottom.
> Roots in the charter: I2 (reality is the source of truth), I3 (no completion without verification), I7 (it gets better the more you use it).

## Why this file exists

Most safety nets protect **artifacts**: tests, linters, render checks. But most of what you actually consume from an AI assistant is **its sentences** - "all 40 tests pass", "that file is not used anywhere", "both copies are identical". Nothing protects those.

There is a second, quieter problem: if wrong claims are not written down anywhere, nobody can count them, and a mistake that recurs every week never looks like a pattern. This file does not fix anything by itself. It makes the need for a fix **visible**.

## Classes

| Class | Definition | Typical form |
|---|---|---|
| **A. Quoted instead of measured** | A value that had to be obtained by running something was copied from a document or memory | Test totals, counts, sizes, versions |
| **B. Broken measurement** | Something was measured, but the measuring tool was broken, so it reported zero or green - and that zero was used as evidence | "0 matches, so it is safe" |
| **C. Partial lookup, total claim** | Only part of the space was looked at, but the claim was about all of it: "N places", "identical", "none" | Number of copies or versions of something |
| **D. Other** | A wrong claim that fits none of the above. Put the root cause on it later with `CODEBOOK.md` | |

If the class is unclear, file it as **D** and reclassify at the next review.

## Entry rules

- **The assistant writes the entries** - including errors that the human, another model or a later observation caught. Being corrected and not recording it is a second mistake.
- **Record as soon as the error is known.** "Tidy it up later" defeats the purpose of the file.
- **Record only errors.** Correct judgements are not logged - exceptions only.
- **Give the source**: file and line, or the session date. An entry with no source is itself a class A error.
- **Start the detector field with one word**: `self` (the assistant itself), `peer` (another model or reviewer), `human`, or `observed` (reality showed it later - including checks that ran unprompted). It is the same list as `CODEBOOK.md` section 3a, so `tools/ledger_count.py claims` can count it. Details go in brackets after the word. One fixed word per entry is what makes "who catches the errors" countable; free prose gets miscounted.
- **Number entries with a tool or a lock if more than one session writes here.** "Last number + 1" counted by hand collides when two sessions append at the same time.

## The negative control rule (read this before writing "zero")

*Negative control* here means a control for the claim "zero" - a planted positive that the same command must find.

Before you claim **"0 found"**, **"none"** or **"no differences"**, show that the measuring tool can find **one**:

1. Plant a known positive (a file containing the word, a line with the pattern, a record with the bad value).
2. Run the same command, unchanged, and show that it reports that one.
3. Only then run it on the real target and report the zero.

A search that cannot find a planted needle has not shown that the haystack is empty. This one rule prevents most class B errors. The same idea applies to tools you build: give every checker a built-in control that must fire, and treat a missing control result as "not measured" rather than "clean".

For the two most common zeros, `tools/negative_control.py` does the three steps for you on a temporary copy (your file is only read):

```
python3 tools/negative_control.py lines   --input app.log --pattern '^ERROR' --plant 'ERROR planted'
python3 tools/negative_control.py csv-sum --input results.csv --column failures
```

Exit 0 prints the real number after the planted case was found; exit 3 means **not measured** (for example, the column is not in the header) and must never be reported as 0. Put the paths that running jobs read in a file and pass it with `--deny-list`, so the tool refuses them.

Also: **say how far you looked, inside the sentence.** Not "the front end never reads the URL" but "inside `index.html` there are 0 places that read the URL". The second form can be refuted by the next person in one line.

## Entry format

```
## <YYYY-MM-DD> | class <A|B|C|D> (<class name>) | detector: <human|peer|self|observed> (<details>)

| | |
|---|---|
| Claim | What was said, as close to verbatim as possible |
| Reality | What was actually true, and how that was established (command, file, observation) |
| Why it happened | One or two lines. Root code from CODEBOOK.md if you use it (R1-R10) |
| Harm | Did the wrong claim actually drive anything (a decision, a deployment, a sent message)? Or was it caught first? |
| Source | file:line or session date |
| Fix | What changed so it does not recur - or "none yet" |
```

## Reviewing the ledger

- **Count, do not judge, weekly**: number of entries, share caught by each detector word, and recurrences after a fix.
- **Decide monthly**: group entries by root cause (`CODEBOOK.md`). For the families that keep recurring *even though a rule already existed*, stop writing more rules and build a **device** instead - a template, a detector, or a hard stop.
- **Retire detectors that never fire**, but tell apart two kinds of silence: a detector that ran and found nothing, and a detector that is not called by anything at all. Only the first kind has earned its silence.

## Entries

<!-- Append new entries below this line, newest last. -->
