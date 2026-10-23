#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_config - the one place that knows where collab-os files live.

READ THE LIMITS FIRST
=====================
C1. Fail-open by design. A missing, unreadable or malformed config never stops
    anything: the built-in defaults are used and the reason is put in
    `warnings` (the session-start hook prints it as its first line). Tools that
    must not run on defaults (for example a publishing gate) have to check
    `source` themselves and refuse "builtin".
C2. Only the seven keys below are understood. Unknown keys are reported in
    `warnings` and otherwise ignored, so a typo in a key name falls back to the
    default for that key - read the warnings.
C3. Values are checked for type only (string). Whether a path exists is not
    checked here; each tool reports missing files itself.
C4. The plugin's shell hook re-implements this lookup in POSIX sh. That copy
    only reads flat `"key": "value"` pairs (no escaped quotes inside values).
    `python3 tools/_config.py --selftest` checks the Python side; keeping the two
    in step is a manual review item (see plugin/collab-os/scripts/charter-context.sh).

Lookup order (first hit wins)
-----------------------------
  1. the file named by the environment variable COLLAB_OS_CONFIG
     - set but missing: warning, built-in defaults (it does NOT fall through to 2)
  2. ~/.config/collab-os/config.json
  3. built-in defaults (below)

Relative paths inside a config file are resolved against the folder that holds
the config file. `~` is expanded. Built-in defaults all live under
~/.config/collab-os/ except the two that belong to Claude Code itself.

Keys
----
  charter          your charter (start from CHARTER-TEMPLATE.md)
  gate_log         where gate exceptions are written (misfires only)
  claim_log        your claim-error ledger (start from ledger/LEDGER-TEMPLATE.md)
  handoff_dir      folder for handoff blocks
  rules_file       the always-loaded rules file (Claude Code: ~/.claude/CLAUDE.md)
  transcripts_dir  Claude Code session transcripts (*.jsonl), read by claim_scan
  lang             "en" or "ko" - language of the hook message

Usage
-----
  python3 tools/_config.py               print the resolved config and its source
  python3 tools/_config.py --get charter print one resolved value
  python3 tools/_config.py --json        machine-readable output
  python3 tools/_config.py --selftest    run the self-test (exit 0 = pass)

Standard library only. Python 3.9+.
"""

import json
import os
import sys
import tempfile

CONFIG_ENV = "COLLAB_OS_CONFIG"
KEYS = ("charter", "gate_log", "claim_log", "handoff_dir", "rules_file", "transcripts_dir", "lang")
PATH_KEYS = KEYS[:-1]
LANGS = ("en", "ko")

DEFAULTS = {
    "charter": "~/.config/collab-os/CHARTER.md",
    "gate_log": "~/.config/collab-os/GATE-LOG.md",
    "claim_log": "~/.config/collab-os/CLAIM-LOG.md",
    "handoff_dir": "~/.config/collab-os/handoffs",
    "rules_file": "~/.claude/CLAUDE.md",
    "transcripts_dir": "~/.claude/projects",
    "lang": "en",
}


class Config(object):
    """Resolved configuration. `values` holds absolute paths (and `lang`)."""

    def __init__(self, values, source, path, warnings):
        self.values = values
        self.source = source        # "env", "home" or "builtin"
        self.path = path            # config file path, or None
        self.warnings = warnings    # list of human-readable strings

    def __getitem__(self, key):
        return self.values[key]

    def get(self, key, default=None):
        return self.values.get(key, default)

    def as_dict(self):
        return {"source": self.source, "path": self.path, "warnings": list(self.warnings),
                "values": dict(self.values)}


def _home(environ):
    return environ.get("HOME") or os.path.expanduser("~")


def _expand(value, base_dir, environ):
    if value == "~" or value.startswith("~/"):
        value = _home(environ) + value[1:]
    if not os.path.isabs(value):
        value = os.path.join(base_dir, value)
    return os.path.normpath(value)


def find_config_file(environ=None):
    """Return (path or None, source, warnings) following the lookup order."""
    environ = os.environ if environ is None else environ
    env = environ.get(CONFIG_ENV)
    if env:
        p = _expand(env, os.getcwd(), environ)
        if os.path.isfile(p):
            return p, "env", []
        return None, "builtin", ["%s is set but the file does not exist: %s - using built-in defaults "
                                 "(not falling back to ~/.config/collab-os/config.json)" % (CONFIG_ENV, p)]
    p = os.path.join(_home(environ), ".config", "collab-os", "config.json")
    if os.path.isfile(p):
        return p, "home", []
    return None, "builtin", ["no config file - using built-in defaults"]


def load_config(environ=None):
    """Resolve the configuration. Never raises for bad input (fail-open, limit C1)."""
    environ = os.environ if environ is None else environ
    path, source, warnings = find_config_file(environ)
    raw = {}
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if not isinstance(raw, dict):
                raise ValueError("top level is not a JSON object")
        except (OSError, ValueError) as exc:
            warnings.append("config file unreadable (%s): %s - using built-in defaults" % (exc.__class__.__name__, path))
            raw, source, path = {}, "builtin", None
    base_dir = os.path.dirname(path) if path else _home(environ)
    values = {}
    for key in KEYS:
        v = raw.get(key, DEFAULTS[key])
        if not isinstance(v, str) or not v.strip():
            warnings.append("key %r is not a non-empty string - using the default" % key)
            v = DEFAULTS[key]
        if key == "lang":
            if v not in LANGS:
                warnings.append("lang %r is not one of %s - using 'en'" % (v, "/".join(LANGS)))
                v = "en"
            values[key] = v
        else:
            # defaults are resolved against HOME, file values against the file's folder
            values[key] = _expand(v, base_dir if key in raw else _home(environ), environ)
    unknown = sorted(k for k in raw if k not in KEYS)
    # an "export" section belongs to the publishing tools and is not a typo
    unknown = [k for k in unknown if k != "export"]
    if unknown:
        warnings.append("unknown key(s) ignored: %s" % ", ".join(unknown))
    return Config(values, source, path, warnings)


# ------------------------------------------------------------------ self-test
def _selftest():
    """Principle: a checker is its own first input. Here: this module's own
    lookup rules are exercised in a throwaway HOME, including the empty and
    broken inputs it claims to survive."""
    fails = []

    def check(name, cond):
        print("  %s %s" % ("ok  " if cond else "FAIL", name))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory(prefix="collab-os-config-test-") as home:
        env = {"HOME": home}
        c = load_config(env)
        check("no file -> builtin", c.source == "builtin" and c.path is None)
        check("no file -> warning names defaults", any("built-in defaults" in w for w in c.warnings))
        check("default charter under HOME", c["charter"] == os.path.join(home, ".config", "collab-os", "CHARTER.md"))

        cfgdir = os.path.join(home, ".config", "collab-os")
        os.makedirs(cfgdir)
        with open(os.path.join(cfgdir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"charter": "docs/CHARTER.md", "lang": "ko", "typo_key": "x"}, fh)
        c = load_config(env)
        check("home file found", c.source == "home")
        check("relative path resolved against config folder", c["charter"] == os.path.join(cfgdir, "docs", "CHARTER.md"))
        check("lang read", c["lang"] == "ko")
        check("unknown key reported", any("typo_key" in w for w in c.warnings))
        check("missing key falls back to default", c["gate_log"] == os.path.join(cfgdir, "GATE-LOG.md"))

        other = os.path.join(home, "other.json")
        with open(other, "w", encoding="utf-8") as fh:
            json.dump({"charter": "~/x/C.md", "lang": "fr"}, fh)
        c = load_config(dict(env, COLLAB_OS_CONFIG=other))
        check("env file wins over home file", c.source == "env" and c.path == other)
        check("tilde expanded", c["charter"] == os.path.join(home, "x", "C.md"))
        check("bad lang -> en with warning", c["lang"] == "en" and any("lang" in w for w in c.warnings))

        c = load_config(dict(env, COLLAB_OS_CONFIG=os.path.join(home, "nope.json")))
        check("env set but missing -> builtin, no fall-through", c.source == "builtin")

        with open(other, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        c = load_config(dict(env, COLLAB_OS_CONFIG=other))
        check("broken json -> builtin with warning", c.source == "builtin" and any("unreadable" in w for w in c.warnings))

        with open(other, "w", encoding="utf-8") as fh:
            fh.write("")
        c = load_config(dict(env, COLLAB_OS_CONFIG=other))
        check("empty file -> builtin with warning", c.source == "builtin")

        with open(other, "w", encoding="utf-8") as fh:
            json.dump({"charter": 5}, fh)
        c = load_config(dict(env, COLLAB_OS_CONFIG=other))
        check("wrong type -> default with warning", c["charter"].endswith("CHARTER.md") and c.warnings)

    print("selftest: %d checks failed" % len(fails))
    return 1 if fails else 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return _selftest()
    if "-h" in argv or "--help" in argv:
        print(__doc__)
        return 0
    c = load_config()
    if "--get" in argv:
        i = argv.index("--get")
        key = argv[i + 1] if i + 1 < len(argv) else ""
        if key not in KEYS:
            print("unknown key: %r (keys: %s)" % (key, ", ".join(KEYS)), file=sys.stderr)
            return 2
        print(c[key])
        return 0
    if "--json" in argv:
        print(json.dumps(c.as_dict(), indent=1, ensure_ascii=False))
        return 0
    for w in c.warnings:
        print("warning: " + w)
    print("source: %s%s" % (c.source, (" (%s)" % c.path) if c.path else ""))
    for k in KEYS:
        print("  %-16s %s" % (k, c[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
