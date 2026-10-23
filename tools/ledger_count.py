#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_count - counts your claim-error ledger by class and detector, your gate
log by exception type, and CASES.md by detector, always with the denominator.

READ THE LIMITS FIRST. A checker that hides its limits gets read as "pass = safe".
=================================================================================
K1. It reads headings and labelled fields, not meaning. An entry whose detector
    is written as free prose that does not START with a known word is counted
    as "unclassifiable" - it is never guessed. Read the unclassifiable list.
K2. Only entries that start with a dated heading (`## YYYY-MM-DD ...`) exist for
    it. Older formats (for example a table at the top of the file) are not
    counted; the report says how many dated headings it saw in total.
K3. "Counted" means a dated heading that carries a class A-D. Headings without a
    class (retractions, notes that record a non-error) are reported as excluded,
    not counted; in the `private` profile a heading that says 철회 (retraction) or
    계급 없음 (no class) is excluded even if a class letter appears in it. In the `private` profile an entry must also carry a number label
    `(N)`; dated headings with a class but no number are reported separately.
K4. Detector words are matched as a PREFIX of the detector text, longest word
    first. A name you use for yourself (or for another person) is not built in:
    pass it with --alias NAME=human so that no personal name lives in this file.
K5. The gate-log count is a count of EXCEPTION rows only. The denominator for a
    rate (how many times the gate ran at all) is not measured by this tool, so
    no rate is printed - "gate runs: not measured".
K6. Counts are as of the moment you run it. A live ledger keeps growing; pin a
    copy (and its sha256, printed in the report) when you quote a number.

Controls run on every invocation: a synthetic ledger, gate log and cases file
with known answers go through the same parser first. If any control is off,
the tool exits 2 and its numbers must not be quoted.

Detector vocabulary (one list, shared with ledger/LEDGER-TEMPLATE.md and
ledger/CODEBOOK.md):
  self      the assistant itself (any session, sub-agent or surface of it)
  peer      an independent second model or reviewer
  human     a person
  observed  reality showed it later (a runtime measurement, a watchdog, a check
            that ran unprompted)
Anything else is "unclassifiable".

Profiles
--------
  public   headings as in LEDGER-TEMPLATE.md:
           ## 2026-01-31 | class B (broken measurement) | detector: peer (...)
  private  Korean headings, numbered:
           ## 2026-01-31 (12) | 계급 B (...) | 탐지자: 코덱스 (...)
           Detector may also sit in a bullet "- **탐지자**: ..." under the heading.
           Korean operating words: 자기=self, 코덱스=peer, 사람=human, 실측=observed
           (plus the synonyms listed in KO_SYNONYMS below).

Exit codes
----------
  0  counted
  1  nothing to count (file missing, or zero counted rows) - never reported as "0"
  2  a control failed, bad arguments, or the tool crashed

Usage
-----
  python3 tools/ledger_count.py claims [--file F] [--profile public|private] [--alias NAME=WORD ...]
                                       [--list-unclassified] [--json]
  python3 tools/ledger_count.py gates  [--file F] [--json]
  python3 tools/ledger_count.py cases  [--file CASES.md] [--json]
  python3 tools/ledger_count.py --selftest
  --file defaults to claim_log / gate_log from tools/_config.py (cases: ./CASES.md).

Standard library only. Python 3.9+.
"""

import collections
import hashlib
import json
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_BROKEN = 2

DETECTORS = ("self", "peer", "human", "observed")
CLASSES = ("A", "B", "C", "D")
UNCLASSIFIABLE = "unclassifiable"

EN_SYNONYMS = {
    "self": "self", "peer": "peer", "human": "human", "observed": "observed",
}
# Korean operating vocabulary and the synonyms seen in practice. Prefix match,
# longest first. Product names are generic; personal names never go here (K4).
KO_SYNONYMS = {
    "자기": "self", "claude": "self", "code": "self", "하위": "self", "서브에이전트": "self",
    "채팅": "self", "chat": "self", "다른 창": "self", "다른 세션": "self",
    "코덱스": "peer", "codex": "peer", "외부": "peer",
    "사람": "human",
    "실측": "observed", "워치독": "observed", "나중": "observed",
}

DATE_HEAD = re.compile(r"^## (20\d\d-\d\d-\d\d)\b(.*)$")
PUB_CLASS = re.compile(r"\|\s*class\s+([A-D])\b", re.I)
PUB_DET = re.compile(r"\|\s*detector\s*:\s*(.*)$", re.I)
PRIV_NUM = re.compile(r"^\s*\((\d+[a-z]?(?:-[a-z])?)\)")
PRIV_CLASS = re.compile(r"계급\s*([A-D])\b")
PRIV_EXCLUDE = re.compile(r"철회|계급\s*없음")
PRIV_DET_HEAD = re.compile(r"탐지자\**\s*:?\**\s*:?\s*(.*)$")
PRIV_DET_BULLET = re.compile(r"^\s*[-*]\s*\*\*탐지자\*\*\s*:?\s*(.*)$")
LEAD_JUNK = re.compile(r"^[\s*_`\"'(「\[]+")


# ------------------------------------------------------------------ detector words
def build_vocab(profile, aliases=None):
    vocab = dict(EN_SYNONYMS)
    if profile == "private":
        vocab.update(KO_SYNONYMS)
    for k, v in (aliases or {}).items():
        vocab[k.lower()] = v
    return sorted(vocab.items(), key=lambda kv: -len(kv[0]))


def classify_detector(text, vocab):
    """Return (detector word or None, reason). Prefix match on the cleaned text."""
    if text is None:
        return None, "no detector field"
    t = LEAD_JUNK.sub("", text).strip().lower()
    if not t:
        return None, "empty detector field"
    for key, word in vocab:
        if t.startswith(key):
            return word, ""
    return None, "first word not in the vocabulary"


# ------------------------------------------------------------------ claims
def parse_claims(text, profile, aliases=None):
    vocab = build_vocab(profile, aliases)
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if DATE_HEAD.match(l)] + [len(lines)]
    res = {"dated_headings": len(starts) - 1, "counted": 0, "excluded_no_class": 0,
           "unnumbered_with_class": 0, "classes": collections.Counter(),
           "detectors": collections.Counter(), "unclassifiable": []}
    for k in range(len(starts) - 1):
        block = lines[starts[k]:starts[k + 1]]
        head = block[0]
        rest = DATE_HEAD.match(head).group(2)
        label = None
        if profile == "public":
            mc, md = PUB_CLASS.search(head), PUB_DET.search(head)
            det_text = md.group(1) if md else None
        else:
            mn = PRIV_NUM.match(rest)
            label = mn.group(1) if mn else None
            # same rule as the private extractor: a retraction or a "no class" record is not an error
            mc = None if PRIV_EXCLUDE.search(head) else PRIV_CLASS.search(head)
            md = PRIV_DET_HEAD.search(head)
            det_text = md.group(1) if md else None
            if det_text is None:
                for l in block[1:]:
                    mb = PRIV_DET_BULLET.match(l)
                    if mb:
                        det_text = mb.group(1)
                        break
        if not mc:
            res["excluded_no_class"] += 1
            continue
        if profile == "private" and label is None:
            res["unnumbered_with_class"] += 1
            continue
        res["counted"] += 1
        res["classes"][mc.group(1).upper()] += 1
        word, why = classify_detector(det_text, vocab)
        if word:
            res["detectors"][word] += 1
        else:
            res["detectors"][UNCLASSIFIABLE] += 1
            res["unclassifiable"].append({"line": starts[k] + 1, "label": label, "reason": why})
    return res


def pct(n, d):
    return "%d%%" % round(n * 100.0 / d) if d else "n/a"


def report_claims(res, out):
    n = res["counted"]
    out("dated headings seen     : %d" % res["dated_headings"])
    out("excluded (no class)     : %d" % res["excluded_no_class"])
    if res["unnumbered_with_class"]:
        out("not counted (no number) : %d" % res["unnumbered_with_class"])
    out("counted entries (denominator): %d" % n)
    if not n:
        out("Nothing counted - this is 'could not count', not '0 wrong claims'.")
        return
    out("classes  : " + " · ".join("%s %d" % (c, res["classes"].get(c, 0)) for c in CLASSES)
        + "  (sum %d)" % sum(res["classes"].values()))
    out("detectors: " + " · ".join("%s %d (%s)" % (d, res["detectors"].get(d, 0), pct(res["detectors"].get(d, 0), n))
                                   for d in DETECTORS + (UNCLASSIFIABLE,))
        + "  (sum %d)" % sum(res["detectors"].values()))
    reasons = collections.Counter(u["reason"] for u in res["unclassifiable"])
    if reasons:
        out("unclassifiable by reason: " + " · ".join("%s %d" % kv for kv in sorted(reasons.items())))


# ------------------------------------------------------------------ gates
GATE_TYPES = {"missed": "missed", "over-fired": "over-fired", "overfired": "over-fired",
              "misjudged": "misjudged", "미발동": "missed", "과발동": "over-fired", "오판정": "misjudged"}
GATE_ROW = re.compile(r"^\|\s*20\d\d-\d\d-\d\d\s*\|")


def parse_gates(text):
    res = {"rows": 0, "types": collections.Counter(), "unclassifiable_rows": []}
    for i, line in enumerate(text.splitlines(), 1):
        if not GATE_ROW.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        res["rows"] += 1
        t = LEAD_JUNK.sub("", cells[2] if len(cells) > 2 else "").lower()
        hit = next((v for k, v in sorted(GATE_TYPES.items(), key=lambda kv: -len(kv[0])) if t.startswith(k)), None)
        if hit:
            res["types"][hit] += 1
        else:
            res["types"][UNCLASSIFIABLE] += 1
            res["unclassifiable_rows"].append(i)
    return res


def report_gates(res, out):
    n = res["rows"]
    out("exception rows (denominator for the split): %d" % n)
    if not n:
        out("No rows read - could not count (is this the right file?).")
        return
    out("missed %d · over-fired %d · misjudged %d · unclassifiable %d  (sum %d)"
        % (res["types"].get("missed", 0), res["types"].get("over-fired", 0), res["types"].get("misjudged", 0),
           res["types"].get(UNCLASSIFIABLE, 0), sum(res["types"].values())))
    out("gate runs: not measured - no rate is printed (limit K5)")


# ------------------------------------------------------------------ cases
CASE_HEAD = re.compile(r"^## (\d+)\.\s")
CASE_LINE = re.compile(r"^Detector:\s*([\w-]+)\s*·\s*Class:\s*([A-D]|N/A)\s*·\s*Root:\s*(R\d{1,2}|N/A)\s*$")


def parse_cases(text):
    res = {"cases": 0, "detectors": collections.Counter(), "classes": collections.Counter(),
           "roots": collections.Counter(), "missing_line": []}
    current, seen = None, set()
    for line in text.splitlines():
        h = CASE_HEAD.match(line)
        if h:
            current = h.group(1)
            res["cases"] += 1
            continue
        m = CASE_LINE.match(line.strip())
        if m and current and current not in seen:
            seen.add(current)
            d = m.group(1).lower()
            res["detectors"][d if d in DETECTORS else UNCLASSIFIABLE] += 1
            res["classes"][m.group(2)] += 1
            res["roots"][m.group(3)] += 1
    res["missing_line"] = res["cases"] - len(seen)
    return res


def report_cases(res, out):
    n = res["cases"]
    out("cases (denominator): %d" % n)
    if not n:
        out("No cases read - could not count.")
        return
    out("detectors: " + " · ".join("%s %d" % (d, res["detectors"].get(d, 0)) for d in DETECTORS + (UNCLASSIFIABLE,))
        + " · no Detector line %d" % res["missing_line"])
    out("classes  : " + " · ".join("%s %d" % kv for kv in sorted(res["classes"].items())))
    out("roots    : " + " · ".join("%s %d" % kv for kv in sorted(res["roots"].items())))


# ------------------------------------------------------------------ controls
CTL_PUBLIC = """# ledger
## 2001-01-01 | class A (quoted) | detector: self (same session)
## 2001-01-02 | class B (broken) | detector: peer (second model)
## 2001-01-03 | class B (broken) | detector: Human (asked a question)
## 2001-01-04 | class C (partial) | detector: observed (log)
## 2001-01-05 | class D (other) | detector: a colleague noticed
## 2001-01-06 | retraction of the entry above | detector: self
"""
CTL_PRIVATE = """# 장부
## 2001-01-01 (1) | 계급 A (인용) | 탐지자: 자기 발견
## 2001-01-02 (2) | 계급 B | 탐지자: Codex 2차 검증
## 2001-01-03 (2-a) | 계급 C | 탐지자: Zq (질문)
## 2001-01-04 (3) | 계급 C
- **탐지자**: 실측(로그)
## 2001-01-05 | 계급 D | 탐지자: 자기
## 2001-01-06 (4) | 철회 — 「계급 C 후보」 등재 취소 | 탐지자: 자기
## 2001-01-07 (5) | 계급 D | 탐지자: 전혀 모르는 말
"""
CTL_GATES = """| date | surface | exception type | what happened | candidate fix |
|---|---|---|---|---|
| 2001-01-01 | cli | missed | x | y |
| 2001-01-02 | cli | **오판정 (x)** | x | y |
| 2001-01-03 | chat | over-fired | x | y |
"""
CTL_CASES = """## 1. One
Detector: peer · Class: N/A · Root: N/A
## 2. Two
Detector: self · Class: B · Root: R3
## 3. Three
no line here
"""


def run_controls():
    """Known answers through the same parsers. Returns list of failures."""
    fails = []
    r = parse_claims(CTL_PUBLIC, "public")
    want = {"counted": 5, "excluded": 1, "classes": {"A": 1, "B": 2, "C": 1, "D": 1},
            "det": {"self": 1, "peer": 1, "human": 1, "observed": 1, UNCLASSIFIABLE: 1}}
    got = {"counted": r["counted"], "excluded": r["excluded_no_class"], "classes": dict(r["classes"]),
           "det": dict(r["detectors"])}
    if got != want or sum(r["detectors"].values()) != r["counted"]:
        fails.append("public claims control: %s" % got)
    r = parse_claims(CTL_PRIVATE, "private", {"zq": "human"})
    got = {"counted": r["counted"], "excluded": r["excluded_no_class"], "unnumbered": r["unnumbered_with_class"],
           "classes": dict(r["classes"]), "det": dict(r["detectors"])}
    want = {"counted": 5, "excluded": 1, "unnumbered": 1, "classes": {"A": 1, "B": 1, "C": 2, "D": 1},
            "det": {"self": 1, "peer": 1, "human": 1, "observed": 1, UNCLASSIFIABLE: 1}}
    if got != want:
        fails.append("private claims control: %s" % got)
    r = parse_gates(CTL_GATES)
    if r["rows"] != 3 or dict(r["types"]) != {"missed": 1, "misjudged": 1, "over-fired": 1}:
        fails.append("gates control: %s" % dict(r["types"]))
    r = parse_cases(CTL_CASES)
    if r["cases"] != 3 or r["missing_line"] != 1 or dict(r["detectors"]) != {"peer": 1, "self": 1}:
        fails.append("cases control: %s" % r)
    if parse_claims("", "public")["counted"] or parse_gates("")["rows"]:
        fails.append("empty input did not give zero rows")
    return fails


def selftest():
    """The checker is its own first input: the controls, then this module's own
    docstring (which shows one public heading and one private heading)."""
    fails = run_controls()
    print("  %s controls (%d failure(s))" % ("ok  " if not fails else "FAIL", len(fails)))
    for f in fails:
        print("       " + f)
    doc = __doc__
    pub = parse_claims("\n".join(l.strip() for l in doc.splitlines()), "public")
    pri = parse_claims("\n".join(l.strip() for l in doc.splitlines()), "private")
    ok = (pub["counted"] == 1 and dict(pub["detectors"]) == {"peer": 1}
          and pri["counted"] == 1 and dict(pri["detectors"]) == {"peer": 1})
    print("  %s own docstring: public %d entry %s, private %d entry %s" % (
        "ok  " if ok else "FAIL", pub["counted"], dict(pub["detectors"]), pri["counted"], dict(pri["detectors"])))
    bad = len(fails) + (0 if ok else 1)
    with tempfile.TemporaryDirectory(prefix="ledger-count-self-") as d:
        rc = main(["claims", "--file", os.path.join(d, "missing.md")], _out=lambda *a: None)
        empty = os.path.join(d, "empty.md")
        open(empty, "w").close()
        rc2 = main(["gates", "--file", empty], _out=lambda *a: None)
    ok = rc == EXIT_NOTHING and rc2 == EXIT_NOTHING
    print("  %s missing file -> exit %d, empty gate log -> exit %d (both must be %d)" % (
        "ok  " if ok else "FAIL", rc, rc2, EXIT_NOTHING))
    bad += 0 if ok else 1
    print("self-test: %d failure(s)" % bad)
    return bad


# ------------------------------------------------------------------ main
def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise ValueError("%s needs a value" % name)
        return argv[i + 1]
    return default


def _aliases(argv):
    out = {}
    for i, a in enumerate(argv):
        if a == "--alias":
            if i + 1 >= len(argv) or "=" not in argv[i + 1]:
                raise ValueError("--alias needs NAME=WORD")
            k, v = argv[i + 1].split("=", 1)
            if v not in DETECTORS:
                raise ValueError("--alias word must be one of %s" % ", ".join(DETECTORS))
            out[k.strip().lower()] = v
    return out


def _default_file(key):
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import _config  # noqa: E402 - the one place that knows paths
    return _config.load_config()[key]


def main(argv=None, _out=print):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        _out(__doc__)
        return EXIT_OK
    if argv[0] == "--selftest":
        return EXIT_BROKEN if selftest() else EXIT_OK
    cmd = argv[0]
    if cmd not in ("claims", "gates", "cases"):
        _out("unknown command: %s (claims, gates, cases, --selftest)" % cmd)
        return EXIT_BROKEN
    try:
        profile = _opt(argv, "--profile", "public")
        if profile not in ("public", "private"):
            raise ValueError("--profile must be public or private")
        aliases = _aliases(argv)
        path = _opt(argv, "--file") or (os.path.join(os.getcwd(), "CASES.md") if cmd == "cases"
                                        else _default_file("claim_log" if cmd == "claims" else "gate_log"))
    except ValueError as exc:
        _out("argument error: %s" % exc)
        return EXIT_BROKEN
    fails = run_controls()
    if fails:
        _out("Controls FAILED - do not quote any number from this run:")
        for f in fails:
            _out("  " + f)
        return EXIT_BROKEN
    if not os.path.isfile(path):
        _out("file: %s (MISSING) - nothing counted (exit 1)" % path)
        return EXIT_NOTHING
    with open(path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("utf-8", "replace")
    _out("file    : %s" % path)
    _out("sha256  : %s" % hashlib.sha256(raw).hexdigest())
    _out("controls: passed")
    if cmd == "claims":
        _out("profile : %s" % profile)
        res = parse_claims(text, profile, aliases)
        n = res["counted"]
        if "--json" in argv:
            _out(json.dumps({k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in res.items()},
                            ensure_ascii=False, indent=1))
        else:
            report_claims(res, _out)
            if "--list-unclassified" in argv:
                for u in res["unclassifiable"]:
                    _out("  line %d  label %s  reason: %s" % (u["line"], u["label"] or "-", u["reason"]))
    elif cmd == "gates":
        res = parse_gates(text)
        n = res["rows"]
        if "--json" in argv:
            _out(json.dumps({k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in res.items()}, indent=1))
        else:
            report_gates(res, _out)
    else:
        res = parse_cases(text)
        n = res["cases"]
        if "--json" in argv:
            _out(json.dumps({k: (dict(v) if isinstance(v, collections.Counter) else v) for k, v in res.items()}, indent=1))
        else:
            report_cases(res, _out)
    return EXIT_OK if n else EXIT_NOTHING


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("\nThe tool itself failed -> exit 2 (not a count).", file=sys.stderr)
        sys.exit(EXIT_BROKEN)
