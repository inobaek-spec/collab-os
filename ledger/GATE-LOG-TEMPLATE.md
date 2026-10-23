# Gate log (template) - exceptions only

> Copy this file to the path your config names as `gate_log` (default `~/.config/collab-os/GATE-LOG.md`) and add one row per exception.
> Roots in the charter: I4 (irreversible things belong to the human) and I8 (start from purpose). The procedure that writes here is the `gate` skill.

## What goes in

**Only exceptions.** A gate that fired when it should have is not logged - logging every normal run turns the file into noise nobody reads. There are three kinds of exception:

| Exception type | Meaning |
|---|---|
| `missed` | A card should have been issued and was not (the work started without one) |
| `over-fired` | A card was issued for a chore, and it got in the way |
| `misjudged` | The card was issued, but a verdict on it (where to do the work, which sources to load) turned out wrong once the work started |

Write the type as the **first word** of the third column, so `tools/ledger_count.py gates` can count it. Details may follow the word.

## Reviewing it

- After a few weeks, ask three questions: Were there at most a couple of misses? Did the gate feel like a nuisance (over-fired)? Did at least one card actually change something (a different place to do the work, a different design after reading the sources, something parked)? If no card ever changed anything, the gate is ceremony.
- `python3 tools/ledger_count.py gates` prints the split by type with its denominator (the number of exception rows). It does not print a rate: how many times the gate ran in total is not measured, so "3 misses" cannot be turned into a percentage yet.
- A miss that you only find later is still a miss. Log it with the date you found it and say so in the row.

## Rows

| Date | Surface | Exception type | What happened | Candidate fix |
|---|---|---|---|---|
