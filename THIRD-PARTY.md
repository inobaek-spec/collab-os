# Third-party material and outside references

This file lists everything in the repository that comes from, or refers to, someone else's work.

## Bundled third-party code

**None.** Every script uses only the Python standard library or POSIX `sh`. There are no vendored packages and no runtime dependencies to install.

## License texts

| Text | Where | Source |
|---|---|---|
| MIT License | `LICENSE` | The standard MIT license text, as published by the Open Source Initiative (https://opensource.org/license/mit) |
| Creative Commons Attribution 4.0 International - summary and link | `LICENSE-docs` | Creative Commons (https://creativecommons.org/licenses/by/4.0/). The full legal code is linked, not copied |

## Products and formats referred to

| Name | How it is used | Notes |
|---|---|---|
| Claude Code (Anthropic) | The plugin targets Claude Code's plugin, skill, command and hook formats; the install steps use the `claude plugin` command line | Claude and Claude Code are trademarks of Anthropic. This project is independent and not affiliated with or endorsed by Anthropic |
| Claude Code session transcripts (`*.jsonl`) | `tools/claim_scan.py` reads the assistant text blocks in these files | Read-only. The record layout is an observed format, not a published contract, and may change between versions |
| Git | `export/leak_check.py --git-metadata` and the docs call the `git` program | Optional |

## Methods and ideas cited

| Idea | Where | Reference |
|---|---|---|
| Cohen's kappa (agreement between two classifiers, corrected for chance) | `ledger/CODEBOOK.md` section 6 | Cohen, J. (1960). A coefficient of agreement for nominal scales. *Educational and Psychological Measurement*, 20(1), 37-46 |
| Negative control (here: a control for the claim "zero" - a planted positive that the same command must find) | `ledger/LEDGER-TEMPLATE.md`, the control runs in `tools/` and `export/` | Adapted from the general experimental practice of running controls alongside a measurement; the name is used in this narrower sense, and no specific source is quoted |

## What is deliberately not here

- **No benchmark figures, prices or model comparisons.** Numbers like these go stale fast and need a dated source; the documents describe how to choose, not what to choose.
- **No quoted text from other projects.** All prose was written for this repository.

If you find something that should be listed here, please open an issue.
