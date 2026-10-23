#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rule_health - three structural checks on your rules: do the source-of-truth
pointers still lead somewhere, are there unexplained gaps in the numbering, and
did the number of rules change against a baseline a human approved?

READ THE LIMITS FIRST. A checker that hides its limits gets read as "pass = safe".
=================================================================================
L1. Pointers: only the form  canonical: `path`  (also "source of truth:" and the
    Korean label 정본:) is recognised. It checks that the path EXISTS - not that the
    file says anything useful or agrees with the rule that points at it.
L2. Numbering: only top-level items of the form  `N. **Title**`  in the rules file
    and headings  `## IN. Title`  in the charter are counted. A rule written any
    other way is invisible to it. A number is "retired" when its line reads
    `N. (retired ...)` / `## IN. (retired ...)` (or the Korean (폐기 ...)).
L3. Baseline: counts only. The same number of rules with different content
    passes. Read the diff of your rules file for content changes - this tool
    only tells you when to look harder.
L4. It cannot tell an approved change from an unapproved one. A baseline
    mismatch means "a human should confirm this", and the human updates the
    baseline file by hand after confirming (it never rewrites an existing
    baseline itself).
L5. Relative pointer paths are resolved against the folder of the file that
    contains them; `~` is expanded. Environment variables are not.

Controls run on every invocation: a synthetic rules text with one dead pointer,
one unexplained gap and one baseline mismatch must produce exactly those three
findings through the same functions. If they do not, the tool exits 2 and its
"clean" result must not be trusted.

Exit codes
----------
  0  measured, no findings
  1  measured, at least one finding (dead pointer, unexplained gap, duplicate
     number, baseline mismatch, missing baseline, or one input file missing)
  2  could not measure: both input files missing, a control failed, or a crash

Usage
-----
  python3 tools/rule_health.py                       use rules_file and charter from config
  python3 tools/rule_health.py --rules F --charter F --baseline F
  python3 tools/rule_health.py --init-baseline       write the baseline once (refuses to overwrite)
  python3 tools/rule_health.py --selftest            controls + own-docstring test

Baseline file (default: rule-baseline.json next to the charter):
  {"principles": 16, "retired": [7], "pointers": 4, "charter_articles": 10}

Example of the recognised formats (also used by --selftest as its first input):
    1. **Look before you assert** - canonical: `no-such-dir/recon.md`
    2. (retired - merged into 1)
    4. **Verify for real**
    ## I1. Outputs are files

Standard library only. Python 3.9+.
"""

import json
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_NOT_MEASURED = 2

POINTER_RX = re.compile(r"(?:canonical|source of truth|정본)\s*:\s*`([^`\n]+)`", re.I)
ITEM_RX = re.compile(r"^\s{0,3}(\d{1,3})\.\s+\*\*(.+?)\*\*", re.M)
ITEM_RETIRED_RX = re.compile(r"^\s{0,3}(\d{1,3})\.\s+\((?:retired|폐기)", re.M | re.I)
ART_RX = re.compile(r"^\s{0,3}#{2,3}\s*I(\d{1,3})\.\s*(.*)$", re.M)
ART_RETIRED_PREFIX = re.compile(r"^\((?:retired|폐기)", re.I)
BASELINE_KEYS = ("principles", "retired", "pointers", "charter_articles")


# ------------------------------------------------------------------ checks
def find_pointers(text, base_dir, home):
    """Return [(raw pointer, resolved path, exists)]."""
    out = []
    for raw in POINTER_RX.findall(text):
        p = raw.strip()
        if p == "~" or p.startswith("~/"):
            p = home + p[1:]
        if not os.path.isabs(p):
            p = os.path.join(base_dir, p)
        p = os.path.normpath(p)
        out.append((raw, p, os.path.exists(p)))
    return out


def numbering(numbers, retired):
    """Return (live numbers sorted, duplicates, unexplained gaps)."""
    seen, dups = set(), []
    for n in numbers:
        if n in seen or n in retired:
            dups.append(n)
        seen.add(n)
    allnums = seen | set(retired)
    gaps = []
    if allnums:
        gaps = [n for n in range(1, max(allnums) + 1) if n not in allnums]
    return sorted(seen), sorted(set(dups)), gaps


def measure(rules_text, rules_dir, charter_text, charter_dir, home):
    """Pure measurement over two texts (either may be None). Returns a dict."""
    m = {"pointers": [], "principles": None, "retired": None, "item_dups": [], "item_gaps": [],
         "charter_articles": None, "art_dups": [], "art_gaps": []}
    if rules_text is not None:
        m["pointers"] += find_pointers(rules_text, rules_dir, home)
        nums = [int(n) for n, _ in ITEM_RX.findall(rules_text)]
        retired = sorted({int(n) for n in ITEM_RETIRED_RX.findall(rules_text)})
        live, dups, gaps = numbering(nums, retired)
        m.update(principles=len(live), retired=retired, item_dups=dups, item_gaps=gaps)
    if charter_text is not None:
        m["pointers"] += find_pointers(charter_text, charter_dir, home)
        arts, art_retired = [], []
        for n, title in ART_RX.findall(charter_text):
            (art_retired if ART_RETIRED_PREFIX.match(title.strip()) else arts).append(int(n))
        live, dups, gaps = numbering(arts, set(art_retired))
        m.update(charter_articles=len(live), art_dups=dups, art_gaps=gaps)
    return m


def findings(m, baseline):
    """Turn a measurement plus a baseline dict (or None) into a list of findings."""
    f = []
    for raw, path, ok in m["pointers"]:
        if not ok:
            f.append("dead pointer: `%s` -> %s does not exist" % (raw, path))
    for n in m["item_dups"]:
        f.append("rules file: number %d is used more than once (or is both live and retired)" % n)
    for n in m["item_gaps"]:
        f.append("rules file: number %d is missing and not marked '(retired ...)'" % n)
    for n in m["art_dups"]:
        f.append("charter: article I%d appears more than once" % n)
    for n in m["art_gaps"]:
        f.append("charter: article I%d is missing and not marked '(retired ...)'" % n)
    if baseline is None:
        f.append("no baseline file - run with --init-baseline once, after a human has read the current rules")
        return f
    cur = {"principles": m["principles"], "retired": m["retired"], "pointers": len(m["pointers"]),
           "charter_articles": m["charter_articles"]}
    for k in BASELINE_KEYS:
        if k not in baseline or cur[k] is None:
            continue
        b, c = baseline[k], cur[k]
        if k == "retired":
            if sorted(b) != sorted(c):
                f.append("baseline: retired numbers %s, now %s - confirm with a human, then edit the baseline" % (sorted(b), c))
        elif c != b:
            how = "grew" if c > b else "shrank"
            f.append("baseline: %s %s from %d to %d - confirm with a human, then edit the baseline" % (k, how, b, c))
    return f


# ------------------------------------------------------------------ controls
CONTROL_RULES = """1. **One** - canonical: `definitely-missing-file.md`
2. **Two**
4. **Four**
"""
CONTROL_CHARTER = "## I1. One\n## I2. Two\n"
CONTROL_BASELINE = {"principles": 2, "retired": [], "pointers": 1, "charter_articles": 2}


def run_controls():
    """Three planted problems must give exactly three findings, one of each kind."""
    with tempfile.TemporaryDirectory(prefix="rule-health-control-") as d:
        m = measure(CONTROL_RULES, d, CONTROL_CHARTER, d, d)
        f = findings(m, CONTROL_BASELINE)
    kinds = sorted(x.split(":")[0] for x in f)
    ok = (len(f) == 3 and any(x.startswith("dead pointer") for x in f)
          and any("number 3 is missing" in x for x in f)
          and any(x.startswith("baseline: principles grew from 2 to 3") for x in f))
    return ok, f, kinds


def selftest():
    """The checker is its own first input: the format example in this docstring
    must parse to exactly the counts the docstring promises."""
    bad = 0
    ok, f, _ = run_controls()
    print("  %s controls: %d findings %s" % ("ok  " if ok else "FAIL", len(f), f))
    bad += 0 if ok else 1

    example = __doc__.split("Example of the recognised formats", 1)[1].split("Standard library only", 1)[0]
    example = "\n".join(line[4:] if line.startswith("    ") else line for line in example.splitlines())
    with tempfile.TemporaryDirectory(prefix="rule-health-self-") as d:
        m = measure(example, d, example, d, d)
    want = {"principles": 2, "retired": [2], "item_gaps": [3], "charter_articles": 1, "dead": 2}
    got = {"principles": m["principles"], "retired": m["retired"], "item_gaps": m["item_gaps"],
           "charter_articles": m["charter_articles"], "dead": sum(1 for _, _, e in m["pointers"] if not e)}
    ok = got == want
    print("  %s own docstring example -> %s (want %s)" % ("ok  " if ok else "FAIL", got, want))
    bad += 0 if ok else 1

    with tempfile.TemporaryDirectory(prefix="rule-health-empty-") as d:
        m = measure("", d, "", d, d)
        f = findings(m, {"principles": 0, "retired": [], "pointers": 0, "charter_articles": 0})
    ok = f == [] and m["principles"] == 0
    print("  %s empty texts -> no crash, %d findings against an all-zero baseline" % ("ok  " if ok else "FAIL", len(f)))
    bad += 0 if ok else 1
    print("self-test: %d failure(s)" % bad)
    return bad


# ------------------------------------------------------------------ main
def _read(path):
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    return None


def _arg(argv, name):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
        raise SystemExit("%s needs a value" % name)
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return EXIT_CLEAN
    if "--selftest" in argv:
        return EXIT_NOT_MEASURED if selftest() else EXIT_CLEAN

    ok, cf, _ = run_controls()
    if not ok:
        print("Controls FAILED - this checker is not working; do not trust its output.")
        for x in cf:
            print("  control finding: " + x)
        return EXIT_NOT_MEASURED

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import _config  # noqa: E402 - the one place that knows paths
    cfg = _config.load_config()
    for w in cfg.warnings:
        print("config: " + w)
    home = os.environ.get("HOME") or os.path.expanduser("~")
    rules = os.path.abspath(os.path.expanduser(_arg(argv, "--rules") or cfg["rules_file"]))
    charter = os.path.abspath(os.path.expanduser(_arg(argv, "--charter") or cfg["charter"]))
    baseline_path = os.path.abspath(os.path.expanduser(
        _arg(argv, "--baseline") or os.path.join(os.path.dirname(charter), "rule-baseline.json")))

    rules_text, charter_text = _read(rules), _read(charter)
    print("rules file : %s%s" % (rules, "" if rules_text is not None else "  (MISSING)"))
    print("charter    : %s%s" % (charter, "" if charter_text is not None else "  (MISSING)"))
    print("baseline   : %s" % baseline_path)
    print("controls   : 3 planted problems found (dead pointer, gap, baseline mismatch)")
    if rules_text is None and charter_text is None:
        print("\nNothing to measure - both input files are missing (exit 2). This is not a clean result.")
        return EXIT_NOT_MEASURED

    m = measure(rules_text, os.path.dirname(rules), charter_text, os.path.dirname(charter), home)
    cur = {"principles": m["principles"], "retired": m["retired"], "pointers": len(m["pointers"]),
           "charter_articles": m["charter_articles"]}

    if "--init-baseline" in argv:
        if os.path.exists(baseline_path):
            print("\nA baseline already exists; not overwriting it. Edit it by hand after a human confirms the change.")
            return EXIT_FINDINGS
        with open(baseline_path, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, indent=1)
            fh.write("\n")
        print("\nBaseline written: %s" % json.dumps(cur))
        return EXIT_CLEAN

    baseline = None
    if os.path.isfile(baseline_path):
        try:
            with open(baseline_path, "r", encoding="utf-8") as fh:
                baseline = json.load(fh)
            if not isinstance(baseline, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            print("baseline unreadable (%s) - treated as missing" % exc)
            baseline = None

    print("\nCurrent counts: %s" % json.dumps(cur))
    print("Pointers found: %d" % len(m["pointers"]))
    for raw, path, exists in m["pointers"]:
        print("  %s %s" % ("ok  " if exists else "DEAD", raw))
    f = findings(m, baseline)
    if rules_text is None:
        f.append("rules file missing - its numbering and pointers were not measured")
    if charter_text is None:
        f.append("charter missing - its articles and pointers were not measured")
    print("\nFindings: %d" % len(f))
    for x in f:
        print("  - " + x)
    if f:
        print("\nReport these to the human; do not fix the baseline on your own.")
        return EXIT_FINDINGS
    print("\nClean: pointers alive, no unexplained gaps, counts match the baseline (limits L1-L5 apply).")
    return EXIT_CLEAN


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("\nThe tool itself failed -> exit 2 (not a measurement).", file=sys.stderr)
        sys.exit(EXIT_NOT_MEASURED)
