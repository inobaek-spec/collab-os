#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""claim_scan - a thermometer for one habit: when the assistant states a number,
does it say the scope (denominator, where it looked, what it measured with, how
sure it is) in the same breath?

READ THE LIMITS FIRST. A checker that hides its limits gets read as "pass = safe".
=================================================================================
H1. Claude Code transcripts only. It reads *.jsonl files in `transcripts_dir`
    (from tools/_config.py) and in its immediate subfolders. Conversations on
    other surfaces are not in those files and are not measured.
H2. Sub-agent text appears in the transcripts only if your Claude Code version
    writes it there. The report prints how many sub-agent (sidechain) records it
    saw; if that is 0, only the main session was measured.
H3. A live session keeps growing while you measure, so absolute counts do not
    repeat. Compare before/after only on finished sessions, or freeze a live one
    with --until <ISO time>.
H4. The bias is optimistic. Marker patterns accept some sentences that do not
    really state a scope (a marker on the neighbouring line, "about" in front of
    something that is not an estimate). Read the "bare" share as a LOWER bound.
H5. It does not judge truth. A wrong number with a scope counts as "with scope".
    This measures a writing habit, not accuracy.
H6. thinking, tool_use and tool_result blocks are not read (the report says how
    many text blocks it did read).
H7. Splitting is rule-based: a sentence, or a whole line for tables and bullets.
    The unit is a sentence, not a claim.
H8. The "instrument" marker counts only inside the same sentence. Counting the
    neighbouring line too would let almost everything pass.
H9. Patterns exist for English and Korean only. Numbers in other languages are
    mostly not detected, so the denominator shrinks - the report prints it.
H10. It is a thermometer, not a gate: a bad ratio still exits 0.

Controls run on every measurement: 17 synthetic sentences. The ones known to be
bare numeric claims must be classified "bare", and decoys that do state a scope
must not be. They go through the same parser and classifier as the real data (a temp
.jsonl file). If any control fails, the exit code is 1 and the numbers must not
be quoted.

Exit codes
----------
  0  measured (whatever the ratio)
  1  a control failed, OR nothing could be measured (no files, or zero numeric
     claims) - "measured zero" and "could not measure" are never the same thing
  2  the tool itself crashed

Usage
-----
  python3 tools/claim_scan.py                      all transcripts under transcripts_dir
  python3 tools/claim_scan.py --dir PATH           another folder
  python3 tools/claim_scan.py --files a.jsonl b.jsonl
  python3 tools/claim_scan.py --date 2026-01-31    only records from that local date
  python3 tools/claim_scan.py --until 2026-01-31T12:00:00+00:00
  python3 tools/claim_scan.py --control            run the controls only
  python3 tools/claim_scan.py --selftest           classifier self-test (exit 0 = pass)
  options: --sample N  --no-sidechain  --dump OUT.jsonl  --quiet

Standard library only. Python 3.9+.
"""

import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys
import tempfile

sys.dont_write_bytecode = True

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_TOOL_BROKE = 2

# ------------------------------------------------------------------ masking
# Numbers that are not claims (dates, times, versions, identifiers, paths) are
# replaced by a mask character before claims are searched for.
CODE_FENCE = re.compile(r"```.*?```", re.S)
CODE_SPAN = re.compile(r"`[^`\n]{1,400}`")
URL = re.compile(r"https?://\S+")
ISO_TS = re.compile(r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}([T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?)?")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(?:[ap]\.?m\.?)?", re.I)
EN_DATE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b")
KO_DATE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일|\b\d{1,2}/\d{1,2}\b")
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")
FILELINE = re.compile(r"[\w./\-]+\.(?:py|md|json|ya?ml|sh|html|js|ts|txt|toml|cfg|csv)(?::\d+)?")
LINENO = re.compile(r"\b(?:line|lines|row)\s+\d+\b|\d+\s*(?:행|번째\s*줄)", re.I)
VERSION = re.compile(r"\bv?\d+\.\d+(?:\.\d+)+\b|\bv\d+(?:\.\d+)?\b")
IDENT = re.compile(
    r"(?:\b(?:Principle|principle|Article|article|Rule|rule|Section|section|Step|step|Item|item|Phase|phase)"
    r"|원칙|헌장|조항|규칙|§)\s*[A-Z]?\d+"
    r"|\(\s*\d{1,3}\s*\)"
    r"|\b[A-Z]{1,2}-?\d{1,2}\b"
    r"|#\d+"
)
MONEY = re.compile(r"[$€£₩]\s?[\d,.]+")

MASK = "␀"  # a visible placeholder that never matches a digit or a word

_MASK_RXS = (CODE_FENCE, CODE_SPAN, URL, ISO_TS, CLOCK, EN_DATE, KO_DATE, HEX,
             FILELINE, LINENO, VERSION, IDENT, MONEY)


def mask(text):
    """Blank out numbers that are not claims. Returns (masked text, had code)."""
    had_code = bool(CODE_SPAN.search(text)) or bool(CODE_FENCE.search(text))
    t = text
    for rx in _MASK_RXS:
        t = rx.sub(lambda m: MASK * len(m.group(0)), t)
    return t, had_code


# ------------------------------------------------------------------ numeric claims
NUM = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?"
EN_UNITS = (r"files?|items?|tests?|cases?|lines?|rows?|entries|entry|places?|times|percent|hours?|days?"
            r"|weeks?|months?|errors?|failures?|hits?|matches|match|sessions?|commits?|records?|jobs?"
            r"|checks?|warnings?|copies|copy|versions?|words?|pages?|people|users?|calls?|steps?"
            r"|bugs?|issues?|findings?|paths?|folders?|directories|modules?|functions?|rules?|sentences?|claims?|blocks?")
KO_UNITS = r"건|개|곳|회|명|종|가지|배|항목|파일|칸|줄|번|퍼센트|시간|분기|주|일간|개월"

CLAIM_RXS = (
    re.compile(r"(?:%s)\s*%%" % NUM),
    re.compile(r"(?:%s)\s+(?:[A-Za-z][\w-]*\s+){0,2}(?:%s)\b" % (NUM, EN_UNITS), re.I),
    re.compile(r"(?:%s)\s*(?:%s)" % (NUM, KO_UNITS)),
    re.compile(r"(?:%s)\s*(?:대|vs\.?|:)\s*(?:%s)" % (NUM, NUM)),
    re.compile(r"\b(?:failures?|errors?|missing|violations?|differences?|changes?|duplicates?|leaks?"
               r"|regressions?|mismatch(?:es)?)\b\s*(?:total|count)?\s*[:=]?\s*0(?!\d|[.,]\d)", re.I),
    re.compile(r"(?:실패|누락|오류|에러|결손|위반|사고|차이|변경|중복|불일치)\s*[:은는이가]?\s*0(?!\d|[.,]\d)"),
)


def find_claims(masked):
    hits = []
    for rx in CLAIM_RXS:
        for m in rx.finditer(masked):
            hits.append(m.group(0))
    return hits


# ------------------------------------------------------------------ scope markers
DENOM_RX = re.compile(
    r"(?:%(n)s)\s+(?:[A-Za-z][\w-]*\s+){0,2}(?:out\s+)?of\s+(?:the\s+|all\s+)*(?:%(n)s)"
    r"|\bout\s+of\s+(?:%(n)s)"
    r"|(?:%(n)s)\s*/\s*(?:%(n)s)"
    r"|\bdenominator\b"
    r"|\b(?:total|all|every one) of\s+(?:%(n)s)"
    r"|\b(?:total|all)\s+(?:%(n)s)\s+(?:[A-Za-z][\w-]*\s+){0,2}(?:checked|scanned|searched|examined|read|counted)\b"
    r"|(?:%(n)s)\s*(?:%(ku)s)?\s*중(?![가-힣])"
    r"|중\s*(?:%(n)s)\s*(?:%(ku)s)"
    r"|분모"
    r"|(?:전체|총|모두)\s*(?:%(n)s)\s*(?:%(ku)s)\s*(?:중|을|를|에서|가운데|기준)"
    % {"n": NUM, "ku": KO_UNITS}, re.I)
SCOPE_RX = re.compile(
    r"\b(?:only|just)\s+(?:in|under|within|inside|for|from)\b"
    r"|\b(?:in|inside|under|within)\s+(?:the\s+|this\s+|that\s+)?"
    r"(?:file|folder|directory|repo|repository|tree|branch|session|transcript|sample|project|module)\b"
    r"|\b(?:in|inside|under)\s+␀"
    r"|\blimited to\b|\bscope\b|\bexcluding\b|\bexcept\b|\bbased on\b|\bsampled?\b"
    r"|\bacross all\b|\bfull scan\b|\bat HEAD\b|\bclean clone\b"
    r"|에서는|에서만|안에서|한정|범위\s*는|제외(?:하|한|하고|했)|기준(?:으로|은|:)|대상은|표본은|모수"
    r"|전수(?:를|로|는)?\s*(?:조회|확인|측정|셈)|해당\s*파일|그\s*디렉터리|이\s*세션", re.I)
INSTR_RX = re.compile(
    r"\b(?:grep|rg|git|python3?|pytest|awk|sed|jq|wc|ls|diff)\b|\bfind\s+[./~␀]"
    r"|\bmeasured\b|\bcounted\s+(?:with|by|using)\b|\bby running\b|\b(?:ran|running)\s+␀"
    r"|\baccording to\s+(?:the\s+)?(?:output|log|report)\b"
    # A bare noun such as "checker" or "측정기" is NOT an instrument marker: it must be
    # a statement of how the number was obtained.
    r"|실측|측정했|측정한\s|측정해|재\s*보니|로\s*재[었]?다|세\s*보니|집계|스크립트로|명령으로|로\s*잰", re.I)
LIMIT_RX = re.compile(
    r"\blimit(?:s|ation|ations)?\b|\bnot measured\b|\bunmeasured\b|\bestimat\w*|\bapprox\w*"
    r"|\babout\s+\d|\baround\s+\d|\broughly\b|~\s*\d|\bmay\b|\bmight\b|\bappears? to\b|\bseems?\b"
    r"|\bunverified\b|\buncertain\b|\blower bound\b|\bupper bound\b|\bnot (?:yet )?checked\b"
    r"|\breference value\b|\bat least\b|\bat most\b"
    r"|한계|미측정|못\s*(?:잰|본|셈|셌|재)|추정|대략|약\s*\d|가량|일\s*수\s*있|보인다|보임"
    r"|확인\s*필요|단정하지|표본이\s*작|불확실|모른다|아직\s*안|정확하지\s*않|오차|참고값", re.I)


def scope_markers(sent, neighbor=""):
    """Markers: a=denominator, b=scope, c=instrument (same sentence only, H8), d=limit."""
    both = sent + "\n" + neighbor
    m = []
    if DENOM_RX.search(both):
        m.append("a_denominator")
    if SCOPE_RX.search(both):
        m.append("b_scope")
    if INSTR_RX.search(sent):
        m.append("c_instrument")
    if LIMIT_RX.search(both):
        m.append("d_limit")
    return m


# ------------------------------------------------------------------ splitting
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(가-힣`*])|(?<=다\.)\s*|(?<=요\.)\s*")


def split_units(text):
    """Yield (line index, sentence, all lines). Table rows and bullets stay whole lines."""
    out = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        parts = [p.strip() for p in SENT_SPLIT.split(s) if p and p.strip()]
        for p in (parts or [s]):
            out.append((i, p, lines))
    return out


def classify_text(text):
    """Pure function: classify every sentence of `text` that contains a numeric claim.
    Empty input gives an empty list (the caller must print the zero denominator)."""
    rows = []
    for (li, sent, lines) in split_units(text or ""):
        m_sent, had_code = mask(sent)
        hits = find_claims(m_sent)
        if not hits:
            continue
        nb = []
        if li - 1 >= 0:
            nb.append(lines[li - 1])
        if li + 1 < len(lines):
            nb.append(lines[li + 1])
        m_nb, _ = mask("\n".join(nb))
        mk = scope_markers(m_sent, m_nb)
        rows.append({"sent": sent, "hits": hits, "markers": mk, "bare": not mk, "code_span": had_code})
    return rows


# ------------------------------------------------------------------ parsing
def iter_assistant_text(path, stats=None):
    """Assistant text blocks only; thinking / tool_use / tool_result are skipped (H6)."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except ValueError:
                if stats is not None:
                    stats["unparsable_lines"] += 1
                continue
            if not isinstance(rec, dict) or rec.get("type") != "assistant":
                continue
            msg = rec.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            if not texts:
                continue
            yield {"file": os.path.basename(path), "lineno": lineno, "ts": rec.get("timestamp"),
                   "sidechain": bool(rec.get("isSidechain")), "text": "\n\n".join(texts)}


def _ts(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def local_date(ts):
    d = _ts(ts)
    if d is None:
        return None
    if d.tzinfo is None:
        return d.date().isoformat()
    return d.astimezone().date().isoformat()


# ------------------------------------------------------------------ scanning
def scan(paths, target_date=None, include_sidechain=True, until=None):
    """Return (rows, stats). `until` (ISO) drops records after that moment (H3)."""
    until_dt = _ts(until) if until else None
    rows = []
    stats = collections.Counter()
    stats["files"] = len(paths)
    for p in paths:
        for blk in iter_assistant_text(p, stats):
            stats["assistant_text_blocks"] += 1
            if until_dt is not None:
                k = _ts(blk["ts"])
                if k is None or (k.tzinfo is None) != (until_dt.tzinfo is None) or k > until_dt:
                    stats["blocks_after_until"] += 1
                    continue
            d = local_date(blk["ts"])
            if target_date and d != target_date:
                stats["blocks_other_date"] += 1
                continue
            if blk["sidechain"]:
                if not include_sidechain:
                    stats["blocks_sidechain_skipped"] += 1
                    continue
                stats["blocks_sidechain"] += 1
            stats["blocks_scanned"] += 1
            for r in classify_text(blk["text"]):
                stats["claims"] += 1
                r = dict(r)
                r.update({"file": blk["file"], "jsonl_line": blk["lineno"], "ts": blk["ts"],
                          "date": d, "sidechain": blk["sidechain"]})
                rows.append(r)
    return rows, stats


# ------------------------------------------------------------------ controls
# (name, sentence, expected) - expected True = must be bare, False = must carry a
# scope marker, None = must not be detected as a claim at all.
CONTROLS = [
    ("en-total-quoted", "All 1,204 regression tests pass.", True),
    ("en-places-files", "The pattern appears in 7 places across 6 files.", True),
    ("en-not-watched", "None of the checkers look at these 30 items.", True),
    ("en-zero-failures", "Failures total: 0, so nothing is broken.", True),
    ("en-not-running", "10 jobs are not running.", True),
    ("en-one-left", "Only 1 failing test remains.", True),
    ("ko-zero", "실패 0건으로 정상입니다.", True),
    ("ko-places", "파일 3개에서 5곳을 고쳤습니다.", True),
    ("ko-not-running", "안 도는 작업이 10건입니다.", True),
    ("decoy-en-denominator", "12 of 40 files failed.", False),
    ("decoy-en-instrument", "grep found 3 matches in the repository.", False),
    ("decoy-en-limit", "About 20 sessions, a rough estimate.", False),
    ("decoy-en-inside-file", "Inside `index.html` there are 0 places that read the URL.", False),
    ("decoy-ko-denominator", "40개 중 12개가 실패했습니다.", False),
    ("decoy-identifier", "Principle 8 and article I9 apply.", None),
    ("decoy-file-line", "`report.py:612` was read.", None),
    ("decoy-date-version", "Released 2026-01-31 as v1.2.3 at 14:05.", None),
]


def _verdict(rows):
    return None if not rows else rows[0]["bare"]


def run_controls(verbose=True):
    """Run the controls through the real pipeline: a temp .jsonl -> scan() -> verdicts.
    Returns the number of failed controls."""
    with tempfile.TemporaryDirectory(prefix="claim-scan-control-") as d:
        path = os.path.join(d, "control.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i, (name, text, _) in enumerate(CONTROLS):
                rec = {"type": "assistant", "timestamp": "2001-01-01T00:00:%02dZ" % (i % 60),
                       "message": {"content": [{"type": "text", "text": text}]}}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        rows, stats = scan([path])
    by_sent = collections.defaultdict(list)
    for r in rows:
        by_sent[r["sent"]].append(r)
    bad = 0
    if stats.get("assistant_text_blocks", 0) != len(CONTROLS):
        bad += 1
        if verbose:
            print("  FAIL parser saw %d of %d control blocks" % (stats.get("assistant_text_blocks", 0), len(CONTROLS)))
    for name, text, want in CONTROLS:
        got = _verdict(by_sent.get(text, []))
        ok = got == want
        bad += 0 if ok else 1
        if verbose:
            print("  %s %-22s want=%-5s got=%-5s %s" % ("ok  " if ok else "FAIL", name, want, got, text))
    if verbose:
        print("  controls: %d of %d passed" % (len(CONTROLS) - bad if bad <= len(CONTROLS) else 0, len(CONTROLS)))
    return bad


# ------------------------------------------------------------------ self-test
def selftest():
    """The checker is its own first input: (1) the controls, (2) this module's own
    docstring fed through the full pipeline must yield claims without crashing,
    (3) empty and malformed inputs must be reported as "not measured"."""
    print("self-test 1/3 - controls through the real pipeline")
    bad = run_controls(verbose=True)

    print("self-test 2/3 - own docstring as the first input")
    with tempfile.TemporaryDirectory(prefix="claim-scan-self-") as d:
        path = os.path.join(d, "self.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "assistant", "timestamp": "2001-01-01T00:00:00Z",
                                "message": {"content": [{"type": "text", "text": __doc__}]}}) + "\n")
            f.write("{ this line is not json\n")
            f.write(json.dumps({"type": "user", "message": {"content": "12 files"}}) + "\n")
        rows, stats = scan([path])
        direct = classify_text(__doc__)
        ok = (len(rows) > 0 and len(rows) == len(direct) and stats["unparsable_lines"] == 1
              and stats["assistant_text_blocks"] == 1)
        bad += 0 if ok else 1
        print("  %s docstring -> %d claim sentences (%d bare) via the pipeline, %d when classified directly; "
              "unparsable lines %d; user records ignored"
              % ("ok  " if ok else "FAIL", len(rows), sum(1 for r in rows if r["bare"]), len(direct),
                 stats["unparsable_lines"]))

        print("self-test 3/3 - empty inputs are 'not measured', never 'zero'")
        empty = os.path.join(d, "empty.jsonl")
        open(empty, "w").close()
        rc_empty = run(["--files", empty, "--quiet"], _out=lambda *a, **k: None)
        rc_none = run(["--dir", os.path.join(d, "no-such-folder"), "--quiet"], _out=lambda *a, **k: None)
        ok = rc_empty == EXIT_FAIL and rc_none == EXIT_FAIL
        bad += 0 if ok else 1
        print("  %s empty file -> exit %d, missing folder -> exit %d (both must be %d)"
              % ("ok  " if ok else "FAIL", rc_empty, rc_none, EXIT_FAIL))
    print("self-test: %d failure(s)" % bad)
    return bad


# ------------------------------------------------------------------ report
def report(rows, stats, out, sample=0):
    """Totals and distributions are always printed together: without the denominator a
    zero cannot be told apart from 'could not count'."""
    out("\nTotals")
    for k in ("files", "unparsable_lines", "assistant_text_blocks", "blocks_after_until", "blocks_other_date",
              "blocks_sidechain_skipped", "blocks_scanned", "blocks_sidechain", "claims"):
        out("  %-26s %s" % (k, format(stats.get(k, 0), ",")))
    out("  (thinking, tool_use and tool_result blocks are not counted - limit H6)")
    total = len(rows)
    if total == 0:
        out("\n  Denominator is 0. This is 'could not count anything', not 'zero bare claims'.")
        return
    bare = sum(1 for r in rows if r["bare"])
    out("\n  Numeric-claim sentences (denominator): %s" % format(total, ","))
    out("  with scope : %8s  (%.1f%% of %s)" % (format(total - bare, ","), (total - bare) * 100.0 / total, format(total, ",")))
    out("  bare       : %8s  (%.1f%% of %s)   <- headline" % (format(bare, ","), bare * 100.0 / total, format(total, ",")))
    out("  The bare share is a LOWER bound: the classifier leans optimistic (limit H4).")
    single = collections.Counter(m for r in rows for m in r["markers"])
    out("\n  Markers (one sentence can carry several; denominator %s):" % format(total, ","))
    for k in ("a_denominator", "b_scope", "c_instrument", "d_limit"):
        v = single.get(k, 0)
        out("    %-14s %8s  (%.1f%%)" % (k, format(v, ","), v * 100.0 / total))
    if not stats.get("blocks_sidechain"):
        out("\n  No sub-agent (sidechain) records were seen in these files - main session only (limit H2).")
    if sample:
        bares = [r for r in rows if r["bare"]]
        step = max(1, len(bares) // sample)
        out("\nSample of bare claims (%d):" % min(sample, len(bares)))
        for i, r in enumerate(bares[::step][:sample], 1):
            out("  [%02d] %s %s %s" % (i, r["ts"], r["file"][:12], r["sent"][:300]))


def collect_paths(a):
    if a.files:
        return list(a.files)
    d = a.dir
    if not d:
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.insert(0, here)
        import _config  # noqa: E402 - the one place that knows paths
        cfg = _config.load_config()
        d = cfg["transcripts_dir"]
    if not os.path.isdir(d):
        return []
    return sorted(set(glob.glob(os.path.join(d, "*.jsonl")) + glob.glob(os.path.join(d, "*", "*.jsonl"))))


def build_parser():
    ap = argparse.ArgumentParser(description="Thermometer: share of numeric claims stated with a scope "
                                             "(not a gate - a bad ratio still exits 0)")
    ap.add_argument("--dir", default=None, help="folder of *.jsonl transcripts (default: transcripts_dir from config)")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD, local date of the record")
    ap.add_argument("--until", default=None, help="ISO time; records after it are dropped (limit H3)")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--control", action="store_true", help="run the controls only")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--no-sidechain", action="store_true")
    ap.add_argument("--dump", default=None, help="write every classified sentence to this .jsonl")
    ap.add_argument("--quiet", action="store_true")
    return ap


def run(argv=None, _out=print):
    a = build_parser().parse_args(argv)
    if a.selftest:
        return EXIT_FAIL if selftest() else EXIT_OK
    _out("Controls (must all pass before any number is quoted):")
    ctrl_bad = run_controls(verbose=not a.quiet or a.control)
    if a.control:
        return EXIT_FAIL if ctrl_bad else EXIT_OK
    paths = collect_paths(a)
    _out("\nInput files: %d" % len(paths))
    if not a.quiet:
        for p in paths[:20]:
            try:
                _out("  - %s  %sB" % (os.path.basename(p), format(os.path.getsize(p), ",")))
            except OSError as exc:
                _out("  - %s  unreadable: %s" % (p, exc))
        if len(paths) > 20:
            _out("  ... and %d more" % (len(paths) - 20))
    if not paths:
        _out("\nNo input files - nothing measured (exit 1). This is not 'zero bare claims'.")
        return EXIT_FAIL
    rows, stats = scan(paths, a.date, include_sidechain=not a.no_sidechain, until=a.until)
    if a.quiet:
        n = len(rows)
        b = sum(1 for r in rows if r["bare"])
        if n:
            _out("  denominator %s | bare %s (%.1f%%) | with scope %s (%.1f%%) | bare share is a lower bound"
                 % (format(n, ","), format(b, ","), b * 100.0 / n, format(n - b, ","), (n - b) * 100.0 / n))
    else:
        report(rows, stats, _out, sample=a.sample)
    if a.dump:
        with open(a.dump, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        _out("\nWrote %d rows to %s" % (len(rows), a.dump))
    if not rows:
        _out("\nNothing measured (0 numeric claims) -> exit 1.")
        return EXIT_FAIL
    if ctrl_bad:
        _out("\n%d control(s) failed -> exit 1. Do not quote these numbers." % ctrl_bad)
        return EXIT_FAIL
    _out("\nMeasured -> exit 0 (a thermometer: a bad ratio still exits 0).")
    return EXIT_OK


def main():
    try:
        sys.exit(run())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - any crash must be told apart from a measurement
        import traceback
        traceback.print_exc()
        print("\nThe tool itself failed -> exit 2 (this is not a measurement).", file=sys.stderr)
        sys.exit(EXIT_TOOL_BROKE)


if __name__ == "__main__":
    main()
