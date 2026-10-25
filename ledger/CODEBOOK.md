# Codebook - root causes of wrong claims (R1-R10)

> Use this with `LEDGER-TEMPLATE.md`. The ledger's classes (A-D) describe **the shape** of a wrong claim; this codebook names **the root** - the one thing that, had it been prevented, would have stopped the error.

## 0. What this is for

For each entry, pick the **one root** such that "if this had been blocked, the error would not have happened".
Then record **whether a rule or an automatic device that should have blocked it already existed at the time**.

The second question is the important one. If a rule already existed and the error happened anyway, **writing another rule is not the answer.**

## 1. Primary root - exactly one

| Code | Name | One-line definition |
|---|---|---|
| **R1** | Did not look | A real thing that could have been opened (file, code, config, log, output) was not opened, or only one layer of it was, and a claim about the world was made anyway |
| **R2** | Repeated without checking | The basis was **someone else's sentence** (a document, a handoff note, a memory entry, a previous session, a sub-agent, a reviewer - or the assistant's own earlier writing), carried over without checking it against reality |
| **R3** | Trusted the instrument | The **output** of a checker, test or command was used as evidence, without confirming what it measures or whether it was broken |
| **R4** | Did not confirm it reaches | Something was built or changed, and nobody confirmed it actually **reaches its target** |
| **R5** | Overstated own work | The **scope of one's own work** was reported as larger than it was ("done" when half done) |
| **R6** | Skipped the procedure | Skipping a required card, approval or gate **was itself** the error, whatever the outcome |
| **R7** | Concurrency or moving state | Several sessions edited at once, numbers collided, or live state moved while it was being read |
| **R8** | Mechanical slip | Not a judgement error: arithmetic, shell quoting, a typo, syntax |
| **R9** | Other | None of the above - **the note must say why** |
| **R10** | Defence never exercised | A guard, gate or safety net was described as working, but nobody ever created the failure on purpose to see it block |

### Decision order - top to bottom, stop at the first match

```
Q1. Right or wrong conclusion aside, is skipping a required procedure or approval the core of the error?   -> R6
Q2. Was the judgement sound and only the execution slipped (arithmetic, quoting, typo)?                    -> R8
Q3. Did concurrent sessions or moving live state cause it?                                                  -> R7
Q4. Was the starting belief "this guard / gate / check protects us", never actually tested?                -> R10
Q5. Where did the wrong belief come from?
    a. someone else's sentence (including the assistant's own past writing)                                -> R2
    b. the output of a tool, checker, test or command                                                       -> R3
    c. an unsourced guess of one's own:
       - "what I built reaches its target"                                                                  -> R4
       - "I have done this much"                                                                            -> R5
       - anything else about the world                                                                      -> R1
```

### Confusable cases

| Situation | Code | Why |
|---|---|---|
| A note said "this updates automatically", so the code was never opened | **R2** | The source was someone's (or one's own past) sentence |
| One `grep` line came back empty, so "it does not exist" | **R1** | Looked only one layer deep. `grep` is a tool, but the problem is the narrow lookup, not a broken tool |
| A checker showed green, but the checker itself was broken | **R3** | The output itself was wrong |
| Five test failures read as "I broke something" - another session was mid-edit | **R7** | The state moved |
| "This reaches all three surfaces" - it reaches two | **R4** | Assumed reach |
| Reported "done" when half done | **R5** | Scope of one's own work |
| "Checked everything" without counting - the target was other paths or files | **R1** | A claim about the world |
| A new rule written without the required approval | **R6** | The procedure itself |
| "The commit hook blocks this" - nobody ever tried to commit the bad case | **R10** | The defence was never exercised |

### Bundled entries

If one ledger entry contains several errors, the unit of classification is still the entry. The primary root is the most frequent one among them; on a tie, the most harmful. Put the rest in `secondary` and `note`.

## 2. Secondary root - optional

If one other root clearly contributed, record it. Otherwise `-`.

## 3. Other fields

| Field | Values | How to decide |
|---|---|---|
| `detector` | `self` / `peer` / `human` / `observed` (see 3a) | If a human's question led the assistant to find it, it is `human` (who created the occasion). Another session of the same assistant is `self`. `observed` covers reality showing it later, including things that run without anyone calling them (scheduled checks, test suites in CI, watchdogs, gates) - an assertion the assistant wrote on the spot is `self`. Anything that fits none of the four is left unclassifiable, not forced |
| `prior_rule` | `Y` / `N` | Was there, **on the date of the error**, an adopted rule that would have prevented it if followed? Use your charter's amendment log for dates |
| `prior_rule_id` | e.g. `I2`, `recon` | Only when Y. If several, the most direct one |
| `prior_device` | `Y` / `N` | Was there an automatic device already running that should have caught it? |
| `prior_device_id` | name of the device | Only when Y |
| `harm` | `real` / `none` / `unknown` | `real` = the wrong thing actually ran: something was sent, data or settings changed, money was spent, or a person **made a decision** on the wrong information. A wrong report caught before any decision is `none` |
| `confidence` | `high` / `medium` / `low` | High if the decision order landed in one pass |
| `note` | one line | **Why this code.** Required for R9 |

### 3a. Detector vocabulary - one list for the ledger, this codebook and the counting tool

The ledger template, this codebook and `tools/ledger_count.py` use the same four words. Why one list: with two lists, the entries the assistant writes and the rows a classifier writes cannot be counted by one tool, and the totals silently disagree.

| Word | Meaning | Korean operating word | Other spellings the `private` profile counts as this word |
|---|---|---|---|
| `self` | The assistant itself - any session, sub-agent or surface of it | 자기 | Claude, Code, 하위, 서브에이전트, 채팅, chat, 다른 창, 다른 세션 |
| `peer` | An independent second model or reviewer | 코덱스 | Codex, 외부 (an outside audit) |
| `human` | A person | 사람 | Your own name or a colleague's name - pass it at run time with `--alias NAME=human`; personal names are never built into the tool |
| `observed` | Reality showed it later: a runtime measurement, a watchdog, a check that ran unprompted | 실측 | 워치독, 나중 |

Words are matched as a prefix of the detector text, longest first. Text that starts with none of them is counted as unclassifiable and listed by line, so it can be fixed at the source.

## 4. Output format

CSV, UTF-8, fixed header:

```
eid,primary,secondary,detector,prior_rule,prior_rule_id,prior_device,prior_device_id,harm,confidence,note
E999,R3,R1,self,Y,I2,N,,none,high,"(format example - not a real entry)"
```

## 5. Two classifiers, independently

Root causes written by the party that made the error are self-reports, and self-reports lean. The fix is two independent classifiers - for example the assistant and a different model, or the assistant and a person:

- Both use **the same codebook and the same frozen snapshot of the ledger** (the live ledger keeps growing).
- Neither looks at the other's file until both are done. Seal each file by writing its hash down right after it is written; editing after seeing the other's answers changes the hash.
- Do not consult each other while classifying. If the codebook is ambiguous, write that in `note` and decide alone - the ambiguity has to show up as a disagreement for the codebook to get fixed.

## 6. Comparing

- Report agreement on the primary root and **Cohen's kappa** (agreement corrected for chance). If kappa is below 0.6 the codebook is too ambiguous: do not finalise the round - fix the codebook first.
- If the fine codes keep splitting along R1/R2/R3 (all three are "believed without checking"), you may compare at the family level - treat R1-R3 as one family and every other code as its own family - and keep the fine codes as a secondary value.
- Show the human only the disagreements, with both verdicts and both notes side by side.
- Compute agreement on `prior_rule` and `prior_device` separately.

## 7. Known limits

- **Entries are not errors.** One entry can hold several errors, and the ledger holds only the errors that were caught. The distribution of uncaught errors is unknown.
- **The ledger is written by the assistant.** "Why it happened" is a self-report. Classifiers should prefer what actually happened (the claim and the reality fields) over the stated reason.
- **Skipping a pilot costs agreement.** Ideally, classify ten entries first and refine the codebook before the full round. Skip it, and expect a lower kappa on the first round.
