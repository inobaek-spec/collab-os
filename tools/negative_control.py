#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""negative_control - before you report "0 found", prove the count could have
found one. It plants a known positive in a TEMPORARY COPY of your file, runs a
fixed read-only check on both copies, and only reports your real number if the
planted one was found.

READ THE LIMITS FIRST. A checker that hides its limits gets read as "pass = safe".
=================================================================================
N1. Two built-in checks only: `lines` (count lines matching a regular
    expression) and `csv-sum` (sum one numeric column of a CSV). It is NOT a
    runner for arbitrary shell commands, and it never runs your own command.
N2. A passing control proves the check can see ONE planted case of the shape you
    planted. It does not prove the check sees every shape of real case, or that
    your pattern or column is the right one to ask about.
N3. Your file is opened read-only and copied; the copies live in a temporary
    folder that is removed on success, failure, timeout, Ctrl-C and SIGTERM. A
    SIGKILL cannot be caught: a folder named negctl-* may then be left in the
    system temp folder - remove it by hand.
N4. The refusal list is only as good as you make it: list every path that a
    running job reads (a scheduler, a service) in a file given by --deny-list
    or by the environment variable COLLAB_OS_DENY_LIST. Inputs at or under a
    listed path are refused before anything is read. Nothing is refused if no
    list is given.
N5. The input is hashed before and after; a change during the run is reported
    as a tool error (the copy may not match what you meant to measure).
N6. csv-sum treats empty and non-numeric cells as 0 and says how many there
    were. A column that is not in the header makes every cell empty, which is
    exactly the "zero that meant could not count" case - the control catches it.

Exit codes
----------
  0  measured: the planted case was found, and the real number is printed
  3  NOT MEASURED: the planted case was not found, the check timed out, or it
     was interrupted - never read this as "0"
  4  refused: the input is on the refusal list (nothing was read)
  2  bad arguments or a tool error

Usage
-----
  python3 tools/negative_control.py lines   --input F --pattern REGEX --plant "LINE THAT MATCHES"
  python3 tools/negative_control.py csv-sum --input F.csv --column NAME [--plant-value 1]
  common options: [--timeout SECONDS (default 30)] [--deny-list FILE]
  python3 tools/negative_control.py --selftest

Standard library only. Python 3.9+.
"""

import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

EXIT_MEASURED = 0
EXIT_ERROR = 2
EXIT_NOT_MEASURED = 3
EXIT_REFUSED = 4
TMP_PREFIX = "negctl-"
DENY_ENV = "COLLAB_OS_DENY_LIST"
TEST_DELAY_ENV = "NEGCTL_TEST_DELAY"  # self-test hook: the worker sleeps this many seconds


class NotMeasured(Exception):
    pass


class Refused(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ the two checks (worker side)
def check_lines(path, pattern):
    rx = re.compile(pattern)
    n = total = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            total += 1
            if rx.search(line):
                n += 1
    return {"count": n, "lines_read": total}


def check_csv_sum(path, column):
    total, rows, empty = 0.0, 0, 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        for row in reader:
            rows += 1
            v = (row.get(column) or "").strip()
            try:
                total += float(v)
            except ValueError:
                empty += 1
    return {"count": total, "rows_read": rows, "empty_or_non_numeric": empty, "column_in_header": column in header}


def worker(argv):
    delay = float(os.environ.get(TEST_DELAY_ENV) or 0)
    if delay:
        time.sleep(delay)
    kind, path, arg = argv
    res = check_lines(path, arg) if kind == "lines" else check_csv_sum(path, arg)
    sys.stdout.write(json.dumps(res))
    return 0


# ------------------------------------------------------------------ driver
def load_deny_list(path):
    paths = []
    for p in [path, os.environ.get(DENY_ENV)]:
        if not p:
            continue
        with open(os.path.expanduser(p), "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    paths.append(os.path.realpath(os.path.expanduser(line)))
    return paths


def refuse_if_listed(inp, deny):
    real = os.path.realpath(inp)
    for d in deny:
        if real == d or real.startswith(d.rstrip(os.sep) + os.sep):
            raise Refused("input %s is at or under a listed path (%s) - something running reads it" % (inp, d))


def run_worker(kind, path, arg, timeout):
    try:
        cp = subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--_worker", kind, path, arg],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise NotMeasured("the check timed out after %s s" % timeout)
    if cp.returncode != 0:
        raise NotMeasured("the check failed: %s" % cp.stderr.decode("utf-8", "replace").strip()[-300:])
    return json.loads(cp.stdout.decode("utf-8"))


def measure(kind, inp, arg, plant, timeout, deny, out):
    refuse_if_listed(inp, deny)
    if not os.path.isfile(inp):
        raise ValueError("input file not found: %s" % inp)
    before = sha256_file(inp)
    tmp = tempfile.mkdtemp(prefix=TMP_PREFIX)
    try:
        ext = os.path.splitext(inp)[1]
        orig = os.path.join(tmp, "original" + ext)
        planted = os.path.join(tmp, "planted" + ext)
        shutil.copyfile(inp, orig)
        with open(orig, "rb") as fh:
            data = fh.read()
        sep = b"" if not data or data.endswith(b"\n") else b"\n"
        if kind == "lines":
            if not re.search(arg, plant):
                raise ValueError("--plant does not match --pattern: a control that cannot fire proves nothing")
            expected = 1
            add = plant.encode("utf-8") + b"\n"
        else:
            expected = float(plant)
            header = next(csv.reader(io.StringIO(data.decode("utf-8", "replace"))), [])
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow([plant if h == arg else "" for h in header] or [""])
            add = buf.getvalue().encode("utf-8")
        with open(planted, "wb") as fh:
            fh.write(data + sep + add)
        r_plant = run_worker(kind, planted, arg, timeout)
        r_orig = run_worker(kind, orig, arg, timeout)
        found = r_plant["count"] - r_orig["count"]
        out("control : planted %s, found %s" % (_num(expected), _num(found)))
        if abs(found - expected) > 1e-9:
            why = ""
            if kind == "csv-sum" and not r_orig.get("column_in_header"):
                why = " - column %r is not in the header, so every value was read as empty" % arg
            raise NotMeasured("the planted case was not found%s" % why)
        after = sha256_file(inp)
        if after != before:
            raise RuntimeError("the input changed during the run (sha256 before %s, after %s)" % (before[:12], after[:12]))
        if kind == "lines":
            out("result  : %d matching line(s) out of %d lines read in %s" % (r_orig["count"], r_orig["lines_read"], inp))
        else:
            out("result  : sum of column %r = %s over %d row(s) (%d empty or non-numeric cell(s)) in %s"
                % (arg, _num(r_orig["count"]), r_orig["rows_read"], r_orig["empty_or_non_numeric"], inp))
        out("input sha256 unchanged: %s" % before)
        return r_orig
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _num(x):
    return str(int(x)) if float(x).is_integer() else "%g" % x


def _opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            raise ValueError("%s needs a value" % name)
        return argv[i + 1]
    return default


def _on_term(signum, frame):
    raise KeyboardInterrupt()


def main(argv=None, out=print):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--_worker"]:
        return worker(argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        out(__doc__)
        return EXIT_MEASURED
    if argv[0] == "--selftest":
        return EXIT_ERROR if selftest() else EXIT_MEASURED
    signal.signal(signal.SIGTERM, _on_term)
    kind = argv[0]
    try:
        if kind not in ("lines", "csv-sum"):
            raise ValueError("unknown check %r (lines, csv-sum)" % kind)
        inp = _opt(argv, "--input")
        if not inp:
            raise ValueError("--input is required")
        timeout = float(_opt(argv, "--timeout", "30"))
        deny = load_deny_list(_opt(argv, "--deny-list"))
        if kind == "lines":
            arg, plant = _opt(argv, "--pattern"), _opt(argv, "--plant")
            if not arg or plant is None:
                raise ValueError("lines needs --pattern and --plant")
            re.compile(arg)
        else:
            arg, plant = _opt(argv, "--column"), _opt(argv, "--plant-value", "1")
            if not arg:
                raise ValueError("csv-sum needs --column")
            float(plant)
        measure(kind, inp, arg, plant, timeout, deny, out)
        out("MEASURED (exit 0)")
        return EXIT_MEASURED
    except Refused as exc:
        out("REFUSED (exit 4): %s" % exc)
        return EXIT_REFUSED
    except NotMeasured as exc:
        out("NOT MEASURED (exit 3): %s. Do not report this as 0." % exc)
        return EXIT_NOT_MEASURED
    except KeyboardInterrupt:
        out("NOT MEASURED (exit 3): interrupted; temporary copies removed.")
        return EXIT_NOT_MEASURED
    except (ValueError, OSError, re.error) as exc:
        out("ERROR (exit 2): %s" % exc)
        return EXIT_ERROR
    except RuntimeError as exc:
        out("ERROR (exit 2): %s" % exc)
        return EXIT_ERROR


# ------------------------------------------------------------------ self-test
def selftest():
    """Covers (a) the missing-column zero, (b) a normal pass, (c) unchanged input,
    (d) timeout, (e) cleanup on success / failure / SIGTERM, (f) refusal - all in
    a throwaway temp root, running this file as a subprocess like a user would."""
    bad = []

    def check(name, cond, detail=""):
        print("  %s %s%s" % ("ok  " if cond else "FAIL", name, ("  [%s]" % detail) if (detail and not cond) else ""))
        if not cond:
            bad.append(name)

    root = tempfile.mkdtemp(prefix="negctl-selftest-")
    try:
        tmpd = os.path.join(root, "tmp")
        os.makedirs(tmpd)
        env = dict(os.environ, TMPDIR=tmpd)
        env.pop(DENY_ENV, None)
        me = [sys.executable, "-B", os.path.abspath(__file__)]

        def run(args, extra=None):
            e = dict(env, **(extra or {}))
            cp = subprocess.run(me + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=e, timeout=120)
            return cp.returncode, cp.stdout.decode("utf-8", "replace")

        def leftovers():
            return [n for n in os.listdir(tmpd) if n.startswith(TMP_PREFIX)]

        data = os.path.join(root, "data")
        os.makedirs(data)
        missing = os.path.join(data, "runs.csv")
        with open(missing, "w", encoding="utf-8") as fh:
            fh.write("id,status\n1,ok\n2,ok\n3,ok\n")
        good = os.path.join(data, "results.csv")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write("id,failures\n1,0\n2,2\n3,1\n")
        log = os.path.join(data, "app.log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write("start\nERROR disk\nok\nERROR net\n")
        h0 = {p: sha256_file(p) for p in (missing, good, log, os.path.abspath(__file__))}

        rc, out = run(["lines", "--input", os.path.abspath(__file__), "--pattern", "^def ", "--plant", "def planted():"])
        check("own source as the first input: measured, exit 0", rc == 0 and "matching line(s)" in out, out)

        rc, out = run(["csv-sum", "--input", missing, "--column", "failures"])
        check("(a) missing column -> NOT MEASURED, exit 3", rc == 3 and "not in the header" in out, out)
        check("(e) cleanup after a failed control", not leftovers(), str(leftovers()))
        rc, out = run(["csv-sum", "--input", good, "--column", "failures"])
        check("(b) csv: control found, real sum 3, exit 0", rc == 0 and "= 3 over 3 row(s)" in out, out)
        rc, out = run(["lines", "--input", log, "--pattern", "^ERROR", "--plant", "ERROR planted"])
        check("(b) lines: control found, real count 2, exit 0", rc == 0 and "2 matching line(s) out of 4" in out, out)
        check("(e) cleanup after success", not leftovers(), str(leftovers()))
        rc, out = run(["lines", "--input", log, "--pattern", "^ERROR", "--plant", "no match here"])
        check("plant that cannot match the pattern is rejected, exit 2", rc == 2, out)
        check("(c) inputs unchanged (sha256 before = after)", h0 == {p: sha256_file(p) for p in h0})
        rc, out = run(["lines", "--input", log, "--pattern", "x", "--plant", "x", "--timeout", "1"],
                      {TEST_DELAY_ENV: "5"})
        check("(d) timeout -> NOT MEASURED, exit 3", rc == 3 and "timed out" in out, out)
        check("(e) cleanup after timeout", not leftovers(), str(leftovers()))

        p = subprocess.Popen(me + ["lines", "--input", log, "--pattern", "x", "--plant", "x", "--timeout", "60"],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=dict(env, **{TEST_DELAY_ENV: "20"}))
        t0 = time.time()
        while not leftovers() and time.time() - t0 < 20:
            time.sleep(0.05)
        saw = bool(leftovers())
        p.send_signal(signal.SIGTERM)
        out = p.communicate(timeout=60)[0].decode("utf-8", "replace")
        check("(e) SIGTERM mid-run -> exit 3 and temp folder removed",
              saw and p.returncode == 3 and not leftovers(), "saw=%s rc=%s left=%s %s" % (saw, p.returncode, leftovers(), out))

        deny = os.path.join(root, "deny.txt")
        with open(deny, "w", encoding="utf-8") as fh:
            fh.write("# paths read by running jobs\n%s\n" % data)
        rc, out = run(["csv-sum", "--input", good, "--column", "failures", "--deny-list", deny])
        check("(f) path on the refusal list -> exit 4, nothing copied", rc == 4 and not leftovers(), out)
        rc, out = run(["csv-sum", "--input", good, "--column", "failures"], {DENY_ENV: deny})
        check("(f) refusal list from the environment variable", rc == 4, out)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("self-test: %d failure(s)" % len(bad))
    return len(bad)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("\nThe tool itself failed -> exit 2 (not a measurement).", file=sys.stderr)
        sys.exit(EXIT_ERROR)
