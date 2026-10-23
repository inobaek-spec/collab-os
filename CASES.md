# Cases - what the pieces actually caught

Short stories from one person's daily work with an AI assistant. Details are generalised on purpose: the point of each case is the *shape* of the failure and the device that caught it, not the particular system.

Each case lists: what happened, what caught it, what changed, and the charter article it maps to. It ends with one line in the ledger's vocabulary: `Detector` (self / peer / human / observed, as in `ledger/CODEBOOK.md` section 3a), `Class` (A-D from `ledger/LEDGER-TEMPLATE.md`, or N/A when the case is not a wrong claim) and `Root` (R1-R10 from the codebook, or N/A).

---

## 1. The one-file exemption

**What happened.** An early version of the launch gate said: chores need no card, and "a small edit to a single file" counts as a chore. It read sensibly. Then a reviewer - a different model, given only the rule text and no surrounding context - was asked to restate it in its own words. It concluded that a one-line change to a file read by a scheduled job needed no card, because it was one file.

**What caught it.** An independent restatement by a reader who did not share the author's assumptions. The author had always pictured "one file" as harmless; the fresh reader took the words literally.

**What changed.** The gate now fires by *where the effect lands*, not by how many files are touched: anything that reaches a running system, people, money or the outside world needs a card, regardless of file count. "When unsure, issue the card" was added at the same time.

**Article.** I4 (irreversible things belong to the human - editing a file a running process reads is a deployment) and I3 (a second pair of eyes that did not write it).

Detector: peer · Class: N/A · Root: R10

---

## 2. The gate that stopped in a sandbox

**What happened.** As a smoke test, a session was asked to "set up a job that emails me a short report every morning, with the script kept in a temporary folder". The session ran in a restricted sandbox: it could not read the charter, the skill files, the existing scheduler entries, or any existing mail-sending code.

**What caught it.** The gate's fallback rule: if the sources cannot be loaded, say so in box 5 and issue a reduced card anyway. The `/collab-os:gate` command text (since folded into the `gate` skill) and the session-start hook message carried that fallback rule themselves, so it still applied even though the skill files were unreadable. The session listed exactly what it had checked and what it could not, issued the five boxes, and stopped. In the process its recon noticed that the temporary folder named in the request is wiped on every reboot - a scheduled job pointing there would silently die after the first restart.

**What changed.** Nothing needed to change; this is the case where the design worked. It is included because the important behaviour is the *stop*: three questions to the human (where should the script live, how should mail be sent, what goes in the message) arrived before any file, schedule or credential existed.

**Article.** I2 (if you cannot check, do not assert), I4 (fail-closed), I8 (purpose before method).

Detector: self · Class: N/A · Root: N/A

---

## 3. Next steps that were already done

**What happened.** A handoff block written at the end of one session told the next session to (a) create a standard format for handoffs and (b) link two rule files together so they could not drift. The receiving session started planning both.

**What caught it.** A single directory listing. The handoff format already existed - it was the very procedure being used - and one of the two rule files was already a pointer to the other.

**What changed.** The handoff procedure gained a "first duty of the receiving side": treat "Next steps" as the sender's guess, check each premise against reality, and report the result before executing. Numbers in a handoff are read as reference values and measured again.

**Article.** I2 (reality is the source of truth). Recon resets when the surface changes: what the sender checked is not what the receiver has checked.

Detector: self · Class: C · Root: R2

---

## 4. The zero that meant "could not count"

**What happened.** A summary of a batch run reported "failures: 0". It was quoted as evidence that the run was clean.

**What caught it.** A later look at the raw data. The summary read a column that did not exist in that data, so every row came back empty, and empty values added up to zero. The instrument was broken, and its broken output looked exactly like a perfect result.

**What changed.** Two habits. One is in the ledger template: the **negative control** - before reporting zero, show that the same command finds one planted case. The other is in the tools' output format: **every zero is printed with its denominator** (`tools/claim_scan.py` prints the denominator next to every share and, when the denominator itself is 0, says it could not count anything) - "0 failures out of 0 rows read" is an alarm; "0 failures" on its own is not.

**Article.** I3 (no completion without verification). Ledger class B: broken measurement.

Detector: observed · Class: B · Root: R3

---

## 5. The checker that flattered itself

**What happened.** The first draft of the claim-scope thermometer (`tools/claim_scan.py`'s ancestor) counted a sentence as "states its scope" if it contained any code formatting. Most sentences that mention a file name wrap it in backticks, so most numeric claims passed.

**What caught it.** Positive controls: a handful of sentences already known to be bare numeric claims - taken from the assistant's own past mistakes - were run through the classifier first. Several came back "with scope".

**What changed.** Code formatting alone no longer counts as a scope marker. The corrected baseline was far less flattering than the first one, and that first number had nearly been recorded as the starting point. The tool now runs its controls on every measurement and refuses to exit 0 if any control fails, and its documentation states that its bias leans optimistic.

**Article.** I3 (verification has a shelf life; the verifier's verdict is not absolute either). The checker was its own first input.

Detector: self · Class: B · Root: R3

## 6. The scope that was declared wrong - and kept

**What happened.** The paste-in install prompt told the assistant to write nothing outside three places: the unzipped folder, `~/.config/collab-os/`, and whatever the `claude plugin` commands write under `~/.claude/plugins/`. The person who wrote that line had not run the commands in a clean home to see where they actually write. `claude plugin marketplace add` also records the marketplace in `~/.claude/settings.json`.

**What caught it.** The tester's assistant. It ran step 2, saw the CLI print "declared in user settings", opened `settings.json`, found a write outside the declared scope, reported the results of steps 1-2 with two options, and stopped before installing. The human who wrote the prompt was the one who was wrong; the assistant that obeyed it was right to stop.

**What changed.** The scope line is now derived from a before/after file listing in a clean home (four places, not one), and the rule for writing such lines is: run the command, diff the tree, then write the sentence. The stop itself was kept as the desired behaviour.

**Article.** I2 (reality beats documents - the prompt was a document) · I4 (the default without approval is "don't" - the stop was correct even though the declared rule was the broken part).

Detector: self · Class: C · Root: R1
