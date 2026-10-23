#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""leak_check - a fail-closed word-floor scanner for a tree you are about to publish.

READ THE LIMITS FIRST. A checker that hides its limits gets read as "pass = safe".
================================================================================
L1. It does not read meaning. Internal stories, schedules, how-we-work details and
    anything that identifies a person without using a listed word pass straight
    through. PASS means "the word floor is clear", never "safe to publish".
    Human and independent semantic review covers this gap.
L2. It only knows the words in your policy file. An unlisted sensitive word is not
    caught. The policy owner adds words; this tool cannot.
L3. Threat model: honest mistakes by a cooperating author, not deliberate hiding.
    Look-alike letters from other scripts, reversed or rot13 text, custom
    encodings, a word split across lines (substring/bounded modes), text inside
    images, hex-encoded text and \uXXXX escapes outside .json files are NOT caught.
L4. It does see through: letter case, NFC/NFD, full-width forms, zero-width and
    other format characters, %XX escapes, HTML entities, \uXXXX escapes in .json
    files, base64 runs of 16-39 characters (decoded), and home-folder paths in
    the forms /Users/<name>, /home/<name>, C:\Users\<name>, -Users-<name>-.
    Base64-like runs of 40+ characters are reported as OPAQUE without decoding:
    if a person cannot read it, it does not ship. Base64 runs shorter than 16
    characters are neither decoded nor reported.
L5. Git metadata (authors, e-mails, messages, tags) is not checked unless you pass
    --git-metadata. A .git folder inside the tree is a finding; --allow-git-dir
    skips ROOT/.git only and says so in the SCOPE line.
L6. Allow-listed binary files are only checked for plain ASCII/UTF-8 byte strings;
    compressed or UTF-16 text inside them is not seen.
L7. Exception contexts are deliberate holes. Every suppressed hit is counted and
    printed, but a real leak hiding inside an exception context passes.
L8. Built-in detectors (home paths, e-mail addresses, key-like tokens) know common
    formats only.
L9. The control run proves that the policy words are caught in the variants listed
    in L4 - nothing more. It does not prove the policy is complete or spelled right.
L10. Normalization tables follow the running Python's Unicode version; rare
     characters may behave differently across Python versions.
L11. Files larger than max_file_bytes are not read; they are reported.
L12. Changes made after the scan are not covered. Re-check right before you publish.

Exit codes
----------
  0   PASS            every planted control was caught and ROOT has no findings
  10  FINDINGS        control passed; ROOT has at least one finding
  20  CONTROL_FAILED  a planted control was missed or a decoy was flagged;
                      ROOT was NOT scanned
  30  CONFIG_ERROR    bad policy, manifest, arguments or paths; nothing scanned
  40  TOOL_ERROR      unexpected internal error, or the control folder (which
                      holds planted policy words) could not be removed; treat
                      as NOT passed
  Anything else (1 = crash before the wrapper, 2 = argument parser, 130 =
  interrupted) also means NOT passed.

Usage
-----
  leak_check.py scan ROOT --policy POLICY.json [--manifest MANIFEST.json]
                [--json-report OUT.json] [--show-matches] [--allow-git-dir]
                [--git-metadata] [--lang en|ko] [--debug]
  leak_check.py control --policy POLICY.json
  leak_check.py digest ROOT [--allow-git-dir]
  leak_check.py --selftest [--policy POLICY.json]
  --show-matches prints raw matches for local diagnosis only: it is refused
  when stdout is not a terminal or when --json-report is given.
"""
# No "from __future__ import annotations": with it, dataclasses look the module up
# in sys.modules, which fails when export.py loads this file by path (Python 3.9).

import argparse
import base64
import binascii
import bisect
import dataclasses
import datetime
import fnmatch
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
import urllib.parse

API_VERSION = 1
TOOL_VERSION = "1.0.0"

EXIT_PASS = 0
EXIT_FINDINGS = 10
EXIT_CONTROL_FAILED = 20
EXIT_CONFIG = 30
EXIT_TOOL = 40
EXIT_INTERRUPTED = 130

POLICY_FORMAT = "collab-os-leak-policy/1"
MANIFEST_FORMAT = "collab-os-export-manifest/1"
REPORT_FORMAT = "collab-os-leak-report/1"
CONFIG_ENV = "COLLAB_OS_CONFIG"

MAX_FILE_BYTES_DEFAULT = 1048576
MAX_FILE_BYTES_MIN = 1024
MAX_FILE_BYTES_MAX = 67108864
BINARY_SNIFF_BYTES = 8192
B64_DECODE_MIN = 16
B64_OPAQUE_MIN = 40
B64_OPAQUE_SEGMENT_MIN = 24
EXCERPT_MAX = 120
TERMINAL_FINDINGS_MAX = 200
GIT_TIMEOUT_S = 60
CONTROL_PREFIX = "leakcheck-control-"
CONTROL_MARKER = "OWNER"
CONTROL_SIGNATURE = "collab-os leak_check control folder v1"
CONTROL_STALE_S = 6 * 3600

JUNK_FILE_GLOBS = (".ds_store", "thumbs.db", "desktop.ini", "*.pyc", "*.pyo",
                   "*.swp", "*.swo", "*~", ".env", ".env.*", "*.orig", "*.rej",
                   "*.log")
JUNK_DIRS = ("__pycache__", ".idea", ".vscode", "node_modules",
             ".ipynb_checkpoints", ".hg", ".svn")
DEFAULT_EMAIL_ALLOW = ("example.com", "example.org", "example.net", "*.example",
                       "*.invalid", "users.noreply.github.com", "git@github.com")
DEFAULT_HOME_USER_ALLOW = ("you", "user", "username", "name", "me", "example")

_MODES = ("substring", "bounded", "separated")
_SEP_SPLIT = re.compile(r"[\s_\-./\\]+")
_SEP_JOIN = r"[\s_\-./\\]*"
_ID_RX = re.compile(r"^W\d{2,3}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_POLICY_KEYS = frozenset(("format", "words", "deny_globs", "email_allow",
                          "home_user_allow", "binary_allow", "opaque_allow",
                          "max_file_bytes"))
_WORD_KEYS = frozenset(("id", "text", "mode", "exceptions", "note"))

# Built-in detectors work on the normalized (case-folded) view.
_HOME_POSIX = re.compile(r"(?<![a-z0-9._\-~])/(?:users|home)/([^/\s'\"`<{$%][^/\s'\"`]*)")
_HOME_WIN = re.compile(r"[a-z]:\\users\\([^\\\s'\"`<{$%][^\\\s'\"`]*)")
_HOME_ENC = re.compile(r"(?:^|[/\s'\"`=(])-users-([a-z0-9._]+)-")
# Each key pattern starts with a literal (no leading look-behind) so the regex
# engine can jump between candidates; the left boundary is checked in Python.
_KEY_SPECS = (
    ("sk-ant-", True, re.compile(r"sk-ant-[a-z0-9_\-]{20,}")),
    ("sk-", True, re.compile(r"sk-(?:proj-)?[a-z0-9_\-]{20,}")),
    ("gh", True, re.compile(r"gh[pousr]_[a-z0-9]{36}")),
    ("akia", True, re.compile(r"akia[0-9a-z]{16}(?![0-9a-z])")),
    ("xox", True, re.compile(r"xox[abprs]-[a-z0-9\-]{10,}")),
    ("aiza", True, re.compile(r"aiza[0-9a-z_\-]{35}")),
    ("-----begin", False, re.compile(r"-----begin [a-z ]*private key-----")),
)
_ALNUM = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")
# Base64-like runs work on the raw text (case matters). The look-behind makes the
# search linear: a run is only tried from its first character.
_B64_RUN = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{%d,}={0,2}" % B64_DECODE_MIN)
_LOCAL_CH = frozenset("abcdefghijklmnopqrstuvwxyz0123456789._%+-")
_DOMAIN_CH = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-.")

_PARTIAL = {"findings": 0}


class ConfigError(Exception):
    """Bad input: policy, manifest, arguments or paths. Exit 30."""


class ToolError(Exception):
    """The tool could not do its job. Exit 40."""


# --------------------------------------------------------------------- config
def _home(environ):
    h = environ.get("HOME")
    return h if h else os.path.expanduser("~")


def expand_user_path(value, environ=None):
    """Expand a leading ~ using HOME from environ (default os.environ)."""
    environ = os.environ if environ is None else environ
    if value == "~":
        return _home(environ)
    if value.startswith("~/"):
        return os.path.join(_home(environ), value[2:])
    return value


def find_config(cli_path=None, environ=None):
    """Return (path or None, source) following: --config, COLLAB_OS_CONFIG,
    ~/.config/collab-os/config.json, built-in. A set-but-missing variable is an
    error; it never falls back to the next location."""
    environ = os.environ if environ is None else environ
    if cli_path:
        p = expand_user_path(cli_path, environ)
        if not os.path.isfile(p):
            raise ConfigError("--config file not found: %s" % p)
        return p, "cli"
    env = environ.get(CONFIG_ENV)
    if env:
        p = expand_user_path(env, environ)
        if not os.path.isfile(p):
            raise ConfigError("%s is set but the file does not exist: %s "
                              "(not falling back to other locations)" % (CONFIG_ENV, p))
        return p, "env"
    p = os.path.join(_home(environ), ".config", "collab-os", "config.json")
    if os.path.isfile(p):
        return p, "home"
    return None, "builtin"


def _pairs_no_dupes(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ConfigError("duplicate key %r" % (k,))
        out[k] = v
    return out


def _no_constants(name):
    raise ConfigError("non-finite number %s is not allowed" % name)


def read_json_strict(path):
    """Return (object, sha256 hex of the raw bytes). Duplicate keys and
    NaN/Infinity are rejected. Raises ConfigError."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ConfigError("cannot read %s: %s" % (path, exc.strerror or exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("%s is not UTF-8" % path)
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        obj = json.loads(text, object_pairs_hook=_pairs_no_dupes,
                         parse_constant=_no_constants)
    except ConfigError as exc:
        raise ConfigError("%s: %s" % (path, exc))
    except ValueError as exc:
        raise ConfigError("%s: invalid JSON: %s" % (path, exc))
    return obj, hashlib.sha256(raw).hexdigest()


def load_json_strict(path):
    return read_json_strict(path)[0]


# --------------------------------------------------------------- normalization
_CF_RX = None


def _cf_regex():
    global _CF_RX
    if _CF_RX is None:
        ranges = []
        start = prev = None
        for cp in range(0x110000):
            if unicodedata.category(chr(cp)) == "Cf":
                if start is None:
                    start = prev = cp
                elif cp == prev + 1:
                    prev = cp
                else:
                    ranges.append((start, prev))
                    start = prev = cp
        if start is not None:
            ranges.append((start, prev))
        cls = "".join(re.escape(chr(a)) if a == b else
                      "%s-%s" % (re.escape(chr(a)), re.escape(chr(b)))
                      for a, b in ranges)
        _CF_RX = re.compile("[" + cls + "]")
    return _CF_RX


def norm(s):
    """Fold case, NFC/NFD, full-width forms and format characters. NFKC runs
    again after format characters are removed: a zero-width character between a
    base letter and its combining mark blocks composition until it is gone."""
    v = unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", s).casefold())
    if not v.isascii():
        stripped = _cf_regex().sub("", v)
        if stripped != v:
            v = unicodedata.normalize("NFKC", stripped)
    return v


# ---------------------------------------------------------------------- policy
@dataclasses.dataclass(frozen=True)
class WordRule:
    id: str
    text: str
    norm: str
    mode: str
    rx: object
    exceptions: tuple   # ((normalized context, (offsets...)), ...)


@dataclasses.dataclass(frozen=True)
class DenyGlob:
    raw: str
    comps: tuple
    anchored: bool
    dir_only: bool


@dataclasses.dataclass(frozen=True)
class Policy:
    words: tuple
    deny: tuple
    email_allow: frozenset
    email_allow_suffixes: tuple
    home_user_allow: frozenset
    binary_allow: dict
    opaque_allow: frozenset
    max_file_bytes: int
    sha256: str
    path: str


def _word_regex(wid, t, mode):
    if mode == "substring":
        return re.compile(re.escape(t))
    if mode == "bounded":
        return re.compile(r"(?<![0-9a-z])" + re.escape(t) + r"(?![0-9a-z])")
    parts = [p for p in _SEP_SPLIT.split(t) if p]
    if not parts:
        raise ConfigError("word %s: separated mode needs letters or digits" % wid)
    return re.compile(_SEP_JOIN.join(re.escape(p) for p in parts))


def parse_deny_glob(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("deny_globs: empty pattern")
    g = unicodedata.normalize("NFC", raw.strip()).casefold()
    if "**" in g:
        raise ConfigError("deny_globs: '**' is not supported (%r)" % raw)
    if "\\" in g:
        raise ConfigError("deny_globs: backslash is not allowed (%r)" % raw)
    anchored = g.startswith("/")
    dir_only = g.endswith("/")
    core = g.strip("/")
    if not core:
        raise ConfigError("deny_globs: pattern has no name (%r)" % raw)
    comps = tuple(core.split("/"))
    for c in comps:
        if c in ("", ".", ".."):
            raise ConfigError("deny_globs: empty, '.' or '..' component (%r)" % raw)
    return DenyGlob(raw.strip(), comps, anchored, dir_only)


def _str_list(obj, key):
    v = obj.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise ConfigError("%s must be a list of non-empty strings" % key)
    return v


def _pin_list(obj, key):
    v = obj.get(key, [])
    if not isinstance(v, list):
        raise ConfigError("%s must be a list" % key)
    out = []
    for i, item in enumerate(v):
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ConfigError("%s[%d] must be {\"path\", \"sha256\"}" % (key, i))
        why = validate_relpath(item["path"]) if isinstance(item["path"], str) else "not a string"
        if why:
            raise ConfigError("%s[%d].path: %s" % (key, i, why))
        if not isinstance(item["sha256"], str) or not _HEX64.match(item["sha256"]):
            raise ConfigError("%s[%d].sha256 must be 64 lowercase hex digits" % (key, i))
        out.append((fold_path(item["path"]), item["sha256"]))
    return out


def policy_from_obj(obj, sha256="", path="<memory>"):
    if not isinstance(obj, dict):
        raise ConfigError("policy must be a JSON object")
    unknown = sorted(set(obj) - _POLICY_KEYS)
    if unknown:
        raise ConfigError("policy: unknown keys %s" % ", ".join(unknown))
    if obj.get("format") != POLICY_FORMAT:
        raise ConfigError("policy: format must be %r" % POLICY_FORMAT)
    words_in = obj.get("words")
    if not isinstance(words_in, list) or not words_in:
        raise ConfigError("policy: words must be a non-empty list (an empty policy cannot pass)")
    words, seen_ids, seen_norm = [], set(), {}
    for i, w in enumerate(words_in):
        if not isinstance(w, dict):
            raise ConfigError("policy: words[%d] must be an object" % i)
        unknown = sorted(set(w) - _WORD_KEYS)
        if unknown:
            raise ConfigError("policy: words[%d]: unknown keys %s" % (i, ", ".join(unknown)))
        wid = w.get("id")
        if not isinstance(wid, str) or not _ID_RX.match(wid):
            raise ConfigError("policy: words[%d].id must look like W01" % i)
        if wid in seen_ids:
            raise ConfigError("policy: duplicate id %s" % wid)
        seen_ids.add(wid)
        text = w.get("text")
        if not isinstance(text, str) or not text:
            raise ConfigError("policy: %s.text must be a non-empty string" % wid)
        t = norm(text)
        if len(t) < 2:
            raise ConfigError("policy: %s.text is shorter than 2 characters after normalization" % wid)
        if t in seen_norm:
            raise ConfigError("policy: %s duplicates %s after normalization" % (wid, seen_norm[t]))
        seen_norm[t] = wid
        mode = w.get("mode", "substring")
        if mode not in _MODES:
            raise ConfigError("policy: %s.mode must be one of %s" % (wid, ", ".join(_MODES)))
        rx = _word_regex(wid, t, mode)
        exc_in = w.get("exceptions", [])
        if not isinstance(exc_in, list):
            raise ConfigError("policy: %s.exceptions must be a list" % wid)
        excs = []
        finder = re.compile("(?=(?:%s))" % rx.pattern)
        for k, e in enumerate(exc_in, 1):
            if not isinstance(e, str) or not e:
                raise ConfigError("policy: %s.exceptions[%d] must be a non-empty string" % (wid, k))
            en = norm(e)
            offs = tuple(m.start() for m in finder.finditer(en))
            if not offs:
                raise ConfigError("policy: %s.exceptions[%d] does not contain the word "
                                  "(an exception context must include it)" % (wid, k))
            excs.append((en, offs))
        note = w.get("note")
        if note is not None and not isinstance(note, str):
            raise ConfigError("policy: %s.note must be a string" % wid)
        words.append(WordRule(wid, text, t, mode, rx, tuple(excs)))
    deny = tuple(parse_deny_glob(g) for g in _str_list(obj, "deny_globs"))
    email_exact, email_suffix = set(), []
    for e in tuple(DEFAULT_EMAIL_ALLOW) + tuple(_str_list(obj, "email_allow")):
        e = e.strip().casefold()
        if e.startswith("*."):
            email_suffix.append(e[1:])
        else:
            email_exact.add(e)
    home_allow = frozenset(norm(u) for u in tuple(DEFAULT_HOME_USER_ALLOW) +
                           tuple(_str_list(obj, "home_user_allow")))
    binary_allow = dict(_pin_list(obj, "binary_allow"))
    opaque_allow = frozenset(_pin_list(obj, "opaque_allow"))
    mfb = obj.get("max_file_bytes", MAX_FILE_BYTES_DEFAULT)
    if isinstance(mfb, bool) or not isinstance(mfb, int) or not (
            MAX_FILE_BYTES_MIN <= mfb <= MAX_FILE_BYTES_MAX):
        raise ConfigError("policy: max_file_bytes must be an integer between %d and %d"
                          % (MAX_FILE_BYTES_MIN, MAX_FILE_BYTES_MAX))
    return Policy(tuple(words), deny, frozenset(email_exact), tuple(email_suffix),
                  home_allow, binary_allow, opaque_allow, mfb, sha256, path)


def load_policy(path):
    obj, sha = read_json_strict(path)
    return policy_from_obj(obj, sha, path)


def example_policy():
    """A synthetic policy (no real words). Literals are assembled at run time so
    this file does not contain the words it plants."""
    canary = "zq" + "canary"
    return {
        "format": POLICY_FORMAT,
        "words": [
            {"id": "W01", "text": canary, "mode": "substring",
             "exceptions": [canary + "ish"], "note": "synthetic"},
            {"id": "W02", "text": "Secret" + "_place", "mode": "separated", "note": "synthetic"},
            {"id": "W03", "text": "43" + "21", "mode": "bounded", "note": "synthetic"},
            {"id": "W04", "text": "\ud3ad\uadc4" + "\uc0c1\uc0ac", "mode": "substring",
             "exceptions": ["\ud070\ud3ad\uadc4" + "\uc0c1\uc0ac\uc810"], "note": "synthetic"},
            {"id": "W05", "text": "/" + "Users" + "/zq" + "user", "mode": "substring",
             "note": "synthetic"},
        ],
        "deny_globs": ["DRAFT-*", "_archive/", "/PRIVATE-LOG.md", "*.jsonl", "notes/raw/"],
        "email_allow": [],
        "home_user_allow": [],
        "binary_allow": [],
        "opaque_allow": [],
        "max_file_bytes": MAX_FILE_BYTES_DEFAULT,
    }


# ------------------------------------------------------------- path rules/globs
def fold_path(p):
    """NFC + casefold - for comparing paths on case-insensitive file systems."""
    return unicodedata.normalize("NFC", p).casefold()


def validate_relpath(p):
    """Return a reason string if p is not an acceptable relative path, else None."""
    if not isinstance(p, str) or not p:
        return "empty path"
    if p.startswith("/"):
        return "absolute path"
    if "\\" in p:
        return "backslash in path"
    if "\x00" in p:
        return "NUL in path"
    nfc = unicodedata.normalize("NFC", p)
    if nfc != p:
        return "path is not NFC (write it as %r)" % nfc
    for ch in p:
        if unicodedata.category(ch) in ("Cc", "Cf"):
            return "control or format character in path"
    if len(p.encode("utf-8")) > 1024:
        return "path longer than 1024 bytes"
    for c in p.split("/"):
        if c in ("", ".", ".."):
            return "empty, '.' or '..' component"
        if c.casefold() == ".git":
            return "'.git' component"
        if len(c.encode("utf-8")) > 255:
            return "component longer than 255 bytes"
    return None


def deny_glob_hits(relpath, globs, is_dir=False):
    """Deny patterns (raw text) that match relpath. Simplified gitignore: a
    trailing '/' matches directories only, a leading '/' anchors at the root,
    otherwise the pattern components may match anywhere in the path."""
    comps = fold_path(relpath).split("/")
    total = len(comps)
    hits = []
    for g in globs:
        n = len(g.comps)
        if n > total:
            continue
        starts = (0,) if g.anchored else range(0, total - n + 1)
        for i in starts:
            if all(fnmatch.fnmatchcase(comps[i + j], g.comps[j]) for j in range(n)):
                last_is_dir = (i + n < total) or is_dir
                if g.dir_only and not last_is_dir:
                    continue
                hits.append(g.raw)
                break
    return hits


def _is_junk_file(name):
    low = name.casefold()
    return any(fnmatch.fnmatchcase(low, g) for g in JUNK_FILE_GLOBS)


# ------------------------------------------------------------------- findings
@dataclasses.dataclass
class Finding:
    cat: str
    code: str
    word: object
    path: str
    line: int
    views: list
    excerpt: str
    detail: str = ""

    def as_dict(self):
        return {"cat": self.cat, "code": self.code, "word": self.word, "path": self.path,
                "line": self.line, "views": list(self.views), "excerpt": self.excerpt,
                "detail": self.detail}


@dataclasses.dataclass(frozen=True)
class ScanOptions:
    allow_git_dir: bool = False
    git_metadata: bool = False
    show_matches: bool = False


@dataclasses.dataclass(frozen=True)
class TreeDigest:
    value: str
    files: tuple   # ((nfc relpath, mode, sha256-or-payload, bytes), ...)


@dataclasses.dataclass
class ScanResult:
    findings: list
    suppressed: dict
    files: int
    dirs: int
    bytes: int
    views_used: set
    digest: object
    notes: list


@dataclasses.dataclass
class ControlResult:
    planted: int = 0
    caught: int = 0
    decoys: int = 0
    decoys_flagged: int = 0
    skipped: dict = dataclasses.field(default_factory=dict)
    missed: list = dataclasses.field(default_factory=list)
    elapsed_ms: int = 0
    stale_removed: int = 0

    @property
    def passed(self):
        return self.planted > 0 and self.caught == self.planted and self.decoys_flagged == 0

    def as_dict(self):
        return {"planted": self.planted, "caught": self.caught, "decoys": self.decoys,
                "decoys_flagged": self.decoys_flagged, "skipped": dict(self.skipped),
                "missed": list(self.missed), "elapsed_ms": self.elapsed_ms,
                "stale_removed": self.stale_removed}


class _Text:
    """A string with a lazily built line index."""
    __slots__ = ("s", "_idx")

    def __init__(self, s):
        self.s = s
        self._idx = None

    def _index(self):
        if self._idx is None:
            idx = [0]
            s = self.s
            i = s.find("\n")
            while i != -1:
                idx.append(i + 1)
                i = s.find("\n", i + 1)
            self._idx = idx
        return self._idx

    def line_of(self, pos):
        return bisect.bisect_right(self._index(), pos)

    def line_span(self, pos):
        idx = self._index()
        n = bisect.bisect_right(idx, pos)
        start = idx[n - 1]
        end = self.s.find("\n", start)
        if end == -1:
            end = len(self.s)
        return start, end


def _home_user_ok(user, policy):
    u = user.rstrip(".,;:)]}>\"'`")
    return (not u) or u in policy.home_user_allow


def _email_allowed(local, domain, policy):
    if (local + "@" + domain) in policy.email_allow or domain in policy.email_allow:
        return True
    return any(domain.endswith(suf) for suf in policy.email_allow_suffixes)


def _find_emails(s):
    out = []
    i = s.find("@")
    while i != -1:
        a = i
        while a > 0 and s[a - 1] in _LOCAL_CH and i - a < 64:
            a -= 1
        b = i + 1
        while b < len(s) and s[b] in _DOMAIN_CH and b - i <= 253:
            b += 1
        local = s[a:i]
        domain = s[i + 1:b].rstrip(".-")
        labels = domain.split(".")
        if local and len(labels) >= 2 and all(0 < len(x) <= 63 for x in labels):
            out.append((a, i + 1 + len(domain), local, domain))
        i = s.find("@", i + 1)
    return out


def _builtin_spans(s, policy):
    """(start, end, code, label) of built-in detector hits in a normalized string."""
    spans = []
    if "/users/" in s or "/home/" in s:
        for m in _HOME_POSIX.finditer(s):
            if not _home_user_ok(m.group(1), policy):
                spans.append((m.start(), m.end(), "H-home", "\u2039H\u203a"))
    if ":\\users\\" in s:
        for m in _HOME_WIN.finditer(s):
            if not _home_user_ok(m.group(1), policy):
                spans.append((m.start(), m.end(), "H-home", "\u2039H\u203a"))
    if "-users-" in s:
        for m in _HOME_ENC.finditer(s):
            if not _home_user_ok(m.group(1), policy):
                spans.append((m.start(1) - 7, m.end(1) + 1, "H-home", "\u2039H\u203a"))
    if "@" in s:
        for a, b, local, domain in _find_emails(s):
            if not _email_allowed(local, domain, policy):
                spans.append((a, b, "E-email", "\u2039E\u203a"))
    for lit, bounded, rx in _KEY_SPECS:
        if lit in s:
            for m in rx.finditer(s):
                st = m.start()
                if bounded and st > 0 and s[st - 1] in _ALNUM:
                    continue
                spans.append((st, m.end(), "K-key", "\u2039K\u203a"))
    return spans


def _all_spans(s, policy):
    spans = []
    for w in policy.words:
        for m in w.rx.finditer(s):
            spans.append((m.start(), m.end(), "W", "\u2039%s\u203a" % w.id))
    spans.extend(_builtin_spans(s, policy))
    return spans


def _mask_text(s, policy):
    spans = sorted(_all_spans(s, policy))
    out, pos = [], 0
    for a, b, _code, lab in spans:
        if a < pos:
            if b > pos:
                pos = b
            continue
        out.append(s[pos:a])
        out.append(lab)
        pos = b
    out.append(s[pos:])
    return "".join(out)


def _excerpt(line, start, end, policy, show):
    lo = max(0, start - 50)
    hi = min(len(line), end + 50)
    if show:
        seg = line[lo:hi]
    else:
        spans = _all_spans(line, policy)
        for a, b, _c, _l in spans:
            if a < lo < b:
                lo = a
            if a < hi < b:
                hi = b
        out, pos = [], lo
        for a, b, _c, lab in sorted(s for s in spans if s[0] >= lo and s[1] <= hi):
            if a < pos:
                if b > pos:
                    pos = b
                continue
            out.append(line[pos:a])
            out.append(lab)
            pos = b
        out.append(line[pos:hi])
        seg = "".join(out)
    seg = seg.replace("\t", " ").replace("\r", " ")
    if len(seg) > EXCERPT_MAX:
        seg = seg[:EXCERPT_MAX] + "..."
    return ("..." if lo > 0 else "") + seg + ("..." if hi < len(line) else "")


class _Sink:
    def __init__(self, policy, show, track=False):
        self.policy = policy
        self.show = show
        self.track = track
        self.findings = {}
        self.suppressed = {}

    def add(self, cat, code, word, path, line, view, excerpt, detail=""):
        key = (cat, code, word, path, line)
        f = self.findings.get(key)
        if f is None:
            self.findings[key] = Finding(cat, code, word, path, line, [view], excerpt, detail)
            if self.track:
                _PARTIAL["findings"] = len(self.findings)
        elif view not in f.views:
            f.views.append(view)

    def suppress(self, word_id, exc_index):
        k = "%s:e%d" % (word_id, exc_index)
        self.suppressed[k] = self.suppressed.get(k, 0) + 1

    def ordered(self):
        return sorted(self.findings.values(), key=lambda f: (f.path, f.line, f.cat, f.code, f.word or ""))


# -------------------------------------------------------------------- detection
def _detect(vt, sink, path, view, fixed_line=None, detail_of_line=None):
    """Policy words + built-in detectors over one normalized view."""
    s = vt.s
    policy = sink.policy
    for w in policy.words:
        for m in w.rx.finditer(s):
            st = m.start()
            suppressed = False
            for ei, (en, offs) in enumerate(w.exceptions, 1):
                for k in offs:
                    if st >= k and s.startswith(en, st - k):
                        suppressed = True
                        break
                if suppressed:
                    sink.suppress(w.id, ei)
                    break
            if suppressed:
                continue
            _record(vt, sink, path, view, "W", "W", w.id, st, m.end(), fixed_line, detail_of_line)
    for a, b, code, _lab in _builtin_spans(s, policy):
        _record(vt, sink, path, view, code[0], code, None, a, b, fixed_line, detail_of_line)


def _record(vt, sink, path, view, cat, code, word, a, b, fixed_line, detail_of_line):
    ls, le = vt.line_span(a)
    line_no = fixed_line if fixed_line is not None else vt.line_of(a)
    detail = ""
    if detail_of_line is not None:
        detail = detail_of_line(vt.line_of(a))
        if detail and not sink.show:
            detail = _mask_text(norm(detail), sink.policy)
    excerpt = _excerpt(vt.s[ls:le], a - ls, min(b, le) - ls, sink.policy, sink.show)
    sink.add(cat, code, word, path, line_no, view, excerpt, detail)


def _is_opaque(core):
    if len(core) < B64_OPAQUE_MIN:
        return False
    if not (re.search(r"[A-Z]", core) and re.search(r"[a-z]", core) and re.search(r"[0-9]", core)):
        return False
    return max(len(seg) for seg in core.split("/")) >= B64_OPAQUE_SEGMENT_MIN


def _json_strings(obj):
    out, stack = [], [("", obj)]
    while stack:
        ptr, cur = stack.pop()
        if isinstance(cur, dict):
            for k in reversed(list(cur)):
                kp = ptr + "/" + str(k).replace("~", "~0").replace("/", "~1")
                stack.append((kp, cur[k]))
                out.append((kp + " (key)", str(k)))
        elif isinstance(cur, list):
            for i in range(len(cur) - 1, -1, -1):
                stack.append(("%s/%d" % (ptr, i), cur[i]))
        elif isinstance(cur, str):
            out.append((ptr or "/", cur))
    return out


def _scan_text(path, text, sink, views_used):
    v0 = _Text(norm(text))
    views_used.add("body")
    _detect(v0, sink, path, "body")
    if "%" in v0.s:
        for i, line in enumerate(v0.s.split("\n"), 1):
            if "%" in line:
                dec = urllib.parse.unquote(line, errors="replace")
                if dec != line:
                    views_used.add("percent")
                    _detect(_Text(norm(dec)), sink, path, "percent", fixed_line=i)
    if "&" in v0.s:
        for i, line in enumerate(v0.s.split("\n"), 1):
            if "&" in line:
                dec = html.unescape(line)
                if dec != line:
                    views_used.add("html")
                    _detect(_Text(norm(dec)), sink, path, "html", fixed_line=i)
    if path.casefold().endswith(".json"):
        try:
            obj = json.loads(text.lstrip("\ufeff"))
        except (ValueError, RecursionError):
            sink.add("S", "S-json-undecodable", None, path, 0, "structure",
                     "JSON could not be parsed, so \\u escapes were not decoded")
        else:
            pairs = _json_strings(obj)
            if pairs:
                views_used.add("json-strings")
                joined = _Text("\n".join(norm(s).replace("\n", " ") for _p, s in pairs))
                ptrs = [p for p, _s in pairs]
                _detect(joined, sink, path, "json-strings", fixed_line=0,
                        detail_of_line=lambda n, ptrs=ptrs: ptrs[n - 1] if 0 < n <= len(ptrs) else "")
    raw = None
    for m in _B64_RUN.finditer(text):
        run = m.group(0)
        core = run.rstrip("=")
        up = re.search(r"[A-Z]", core) is not None
        lo = re.search(r"[a-z]", core) is not None
        if raw is None:
            raw = _Text(text)
        line_no = raw.line_of(m.start())
        if up and lo:
            try:
                decoded = base64.b64decode(core + "=" * (-len(core) % 4), validate=True)
                dtext = decoded.decode("utf-8")
            except (binascii.Error, ValueError, UnicodeDecodeError):
                dtext = None
            if dtext:
                views_used.add("base64")
                _detect(_Text(norm(dtext)), sink, path, "base64", fixed_line=line_no)
        if _is_opaque(core):
            digest = hashlib.sha256(run.encode("ascii")).hexdigest()
            if (fold_path(path), digest) not in sink.policy.opaque_allow:
                sink.add("B", "B-opaque", None, path, line_no, "body",
                         "\u2039B\u203a (%d base64-like characters; sha256 %s)" % (len(run), digest[:12]),
                         digest)


def _check_name(rel, is_dir, sink):
    view = _Text(norm("/" + rel))
    _detect(view, sink, rel, "path", fixed_line=0)
    if sink.policy.deny:
        for g in deny_glob_hits(rel, sink.policy.deny, is_dir):
            shown = g if sink.show else _mask_text(norm(g), sink.policy)
            sink.add("G", "G-deny", None, rel, 0, "path", "deny pattern %r" % shown, shown)
    name = rel.rsplit("/", 1)[-1]
    if any(0xD800 <= ord(ch) <= 0xDFFF or unicodedata.category(ch) in ("Cc", "Cf") for ch in name):
        sink.add("S", "S-bad-name", None, rel, 0, "structure",
                 "name has undecodable bytes or control/format characters")


def _read_regular(abs_path, st, limit):
    fd = os.open(abs_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        fst = os.fstat(fd)
        if not stat.S_ISREG(fst.st_mode) or (fst.st_dev, fst.st_ino) != (st.st_dev, st.st_ino):
            return None
        chunks, total = [], 0
        while total < limit:
            b = os.read(fd, min(1 << 20, limit - total))
            if not b:
                break
            chunks.append(b)
            total += len(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _scan_file(abs_path, rel, st, sink, views_used):
    policy = sink.policy
    try:
        data = _read_regular(abs_path, st, policy.max_file_bytes + 1)
    except OSError as exc:
        sink.add("S", "S-unreadable", None, rel, 0, "structure",
                 "cannot read: %s" % (exc.strerror or exc))
        return 0
    if data is None:
        sink.add("S", "S-race", None, rel, 0, "structure",
                 "file changed between listing and opening")
        return 0
    if len(data) > policy.max_file_bytes:
        sink.add("S", "S-oversize", None, rel, 0, "structure",
                 "larger than max_file_bytes (%d); not read" % policy.max_file_bytes)
        return 0
    text = None
    if b"\x00" not in data[:BINARY_SNIFF_BYTES]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is None:
        pinned = policy.binary_allow.get(fold_path(rel))
        if pinned and pinned == hashlib.sha256(data).hexdigest():
            parts = [m.group(0).decode("ascii") for m in re.finditer(rb"[\x20-\x7e]{4,}", data)]
            parts.append(data.decode("utf-8", "ignore"))
            views_used.add("binary-strings")
            _detect(_Text(norm("\n".join(parts))), sink, rel, "binary-strings", fixed_line=0)
        else:
            sink.add("S", "S-binary", None, rel, 0, "structure",
                     "not UTF-8 text and not pinned in binary_allow; content not judged")
        return len(data)
    _scan_text(rel, text, sink, views_used)
    return len(data)


def _join(rel_dir, name):
    return name if not rel_dir else rel_dir + "/" + name


def scan(root, policy, manifest_paths=None, opts=None, track_partial=False):
    """Read-only scan of ROOT. The control run calls this same function."""
    opts = opts or ScanOptions()
    sink = _Sink(policy, opts.show_matches, track_partial)
    views_used = set(["path"])
    files = dirs = nbytes = 0
    notes = []
    found_nondirs, found_dirs = [], []
    stack = [("", root)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            with os.scandir(abs_dir) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            sink.add("S", "S-unreadable", None, rel_dir or ".", 0, "structure",
                     "cannot list folder: %s" % (exc.strerror or exc))
            continue
        subdirs = []
        for e in entries:
            rel = _join(rel_dir, e.name)
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as exc:
                sink.add("S", "S-unreadable", None, rel, 0, "structure",
                         "cannot stat: %s" % (exc.strerror or exc))
                continue
            mode = st.st_mode
            is_dir = stat.S_ISDIR(mode)
            if is_dir and rel == ".git" and opts.allow_git_dir:
                notes.append("ROOT/.git skipped (--allow-git-dir); git metadata %s"
                             % ("checked separately" if opts.git_metadata else "NOT checked"))
                continue
            _check_name(rel, is_dir, sink)
            if stat.S_ISLNK(mode):
                found_nondirs.append(rel)
                sink.add("S", "S-symlink", None, rel, 0, "structure",
                         "symbolic link (not followed)")
                try:
                    target = os.readlink(e.path)
                except OSError:
                    target = ""
                if target:
                    views_used.add("link-target")
                    probe = _Sink(policy, False)
                    _detect(_Text(norm(target)), probe, rel, "link-target", fixed_line=0)
                    for f in probe.findings.values():
                        sink.add(f.cat, f.code, f.word, rel, 0, "link-target",
                                 "(link target masked)" if not opts.show_matches else target)
            elif is_dir:
                low = e.name.casefold()
                if low == ".git":
                    sink.add("S", "S-git", None, rel, 0, "structure",
                             ".git folder inside the tree (not descended)")
                    continue
                if low in JUNK_DIRS:
                    sink.add("S", "S-junk", None, rel, 0, "structure",
                             "junk folder (not descended)")
                    continue
                dirs += 1
                found_dirs.append(rel)
                subdirs.append((rel, e.path))
            elif stat.S_ISREG(mode):
                files += 1
                found_nondirs.append(rel)
                if st.st_nlink > 1:
                    sink.add("S", "S-hardlink", None, rel, 0, "structure",
                             "hard link (%d names)" % st.st_nlink)
                if _is_junk_file(e.name):
                    sink.add("S", "S-junk", None, rel, 0, "structure", "junk file")
                    continue
                if st.st_size > policy.max_file_bytes:
                    sink.add("S", "S-oversize", None, rel, 0, "structure",
                             "larger than max_file_bytes (%d); not read" % policy.max_file_bytes)
                    continue
                nbytes += _scan_file(e.path, rel, st, sink, views_used)
            else:
                found_nondirs.append(rel)
                sink.add("S", "S-special", None, rel, 0, "structure",
                         "FIFO, socket or device (never opened)")
        for item in reversed(subdirs):
            stack.append(item)
    if manifest_paths is not None:
        expected = {}
        for p in manifest_paths:
            expected[fold_path(p)] = p
        found = {}
        for rel in found_nondirs:
            found[fold_path(rel)] = rel
        for f, rel in found.items():
            if f not in expected:
                sink.add("S", "S-unlisted", None, rel, 0, "structure", "not in the manifest")
        for f, p in expected.items():
            if f not in found:
                sink.add("S", "S-missing", None, p, 0, "structure", "listed in the manifest but missing")
        ancestors = set()
        for f in expected:
            parts = f.split("/")
            for i in range(1, len(parts)):
                ancestors.add("/".join(parts[:i]))
        for d in found_dirs:
            if fold_path(d) not in ancestors:
                sink.add("S", "S-unlisted-dir", None, d, 0, "structure",
                         "folder holds no manifest entry")
    try:
        digest = tree_digest(root, opts.allow_git_dir)
    except ToolError as exc:
        digest = None
        notes.append("digest unavailable: %s" % exc)
    return ScanResult(sink.ordered(), dict(sink.suppressed), files, dirs, nbytes,
                      views_used, digest, notes)


# ---------------------------------------------------------------------- digest
def _sha256_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        h = hashlib.sha256()
        size = 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            h.update(b)
            size += len(b)
        return h.hexdigest(), size
    finally:
        os.close(fd)


def tree_digest(root, allow_git_dir=False):
    """Deterministic digest of every non-folder item under root (mtime, owner and
    empty folders ignored; exec bit as 755/644; names compared as NFC)."""
    items = []
    stack = [("", root)]
    try:
        while stack:
            rel_dir, abs_dir = stack.pop()
            with os.scandir(abs_dir) as it:
                entries = list(it)
            for e in entries:
                rel = _join(rel_dir, e.name)
                st = e.stat(follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    if allow_git_dir and rel == ".git":
                        continue
                    stack.append((rel, e.path))
                    continue
                nfc = unicodedata.normalize("NFC", rel)
                if stat.S_ISLNK(st.st_mode):
                    tgt = unicodedata.normalize("NFC", os.readlink(e.path))
                    items.append((fold_path(rel), nfc, "lnk",
                                  hashlib.sha256(tgt.encode("utf-8", "surrogateescape")).hexdigest(), 0))
                elif stat.S_ISREG(st.st_mode):
                    sha, size = _sha256_file(e.path)
                    items.append((fold_path(rel), nfc, "755" if st.st_mode & 0o111 else "644", sha, size))
                else:
                    items.append((fold_path(rel), nfc, "spc", "-", 0))
    except OSError as exc:
        raise ToolError("cannot compute tree digest of %s: %s" % (root, exc.strerror or exc))
    items.sort(key=lambda x: (x[0], x[1]))
    h = hashlib.sha256()
    for _f, nfc, mode, payload, _size in items:
        kind = "l" if mode == "lnk" else ("s" if mode == "spc" else "f")
        h.update(("%s\0%s\0%s\0%s\n" % (kind, nfc, mode, payload)).encode("utf-8", "surrogateescape"))
    return TreeDigest("sha256:" + h.hexdigest(),
                      tuple((nfc, mode, payload, size) for _f, nfc, mode, payload, size in items))


# --------------------------------------------------------------------- control
@dataclasses.dataclass(frozen=True)
class ControlCase:
    case_id: str
    relpath: str
    kind: str          # plant | decoy | clean
    cat: str
    code: object
    word: object


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _rmtree_control(path):
    shutil.rmtree(path)


def _stale_control_cleanup():
    """Remove leftover control folders of earlier runs. Only folders directly in
    the temp dir that carry our prefix, are real folders owned by us with mode
    0700, hold our marker and whose owner process is gone (or that are old)."""
    tmp = tempfile.gettempdir()
    removed, problems = [], []
    try:
        names = os.listdir(tmp)
    except OSError:
        return removed, problems
    uid = os.getuid()
    now = time.time()
    for name in names:
        if not name.startswith(CONTROL_PREFIX):
            continue
        p = os.path.join(tmp, name)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or stat.S_IMODE(st.st_mode) != 0o700:
            continue
        try:
            with open(os.path.join(p, CONTROL_MARKER), "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if not lines or lines[0] != CONTROL_SIGNATURE:
            continue
        info = dict(x.split("=", 1) for x in lines[1:] if "=" in x)
        try:
            pid = int(info.get("pid", ""))
            created = int(info.get("created", "0"))
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if _pid_alive(pid) and now - created < CONTROL_STALE_S:
            continue
        try:
            _rmtree_control(p)
            removed.append(p)
        except OSError:
            problems.append(p)
    return removed, problems


def _fullwidth(text):
    return "".join(chr(ord(c) + 0xFEE0) if (c.isascii() and c.isalnum()) else c for c in text)


def _pct(text):
    return "".join("%%%02X" % b for b in text.encode("utf-8"))


def _entities(text):
    return "".join("&#%d;" % ord(c) for c in text)


def _json_escape(text):
    out = []
    for c in text:
        cp = ord(c)
        if cp < 0x10000:
            out.append("\\u%04x" % cp)
        else:
            cp -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF)))
    return "".join(out)


def _b64_variant(text):
    body = text.encode("utf-8")
    if len(body) > 23:
        return None
    for left, right in (("x ", " y"), ("Hi ", " Ok"), ("Zz ", " Qq"), ("Mix9 ", " End7"),
                        ("Ab ", " Cd"), ("Quick ", " Brown")):
        data = (left + text + right).encode("utf-8")
        while len(data) < 12:
            data = b"Q" + data + b"q"
        if len(data) > 27:
            continue
        enc = base64.b64encode(data).decode("ascii")
        core = enc.rstrip("=")
        if (len(core) >= B64_DECODE_MIN and re.search(r"[A-Z]", core)
                and re.search(r"[a-z]", core)):
            return enc
    return None


def _glob_sample(comp):
    out, i = [], 0
    while i < len(comp):
        c = comp[i]
        if c == "*":
            out.append("zq")
        elif c == "?":
            out.append("q")
        elif c == "[":
            j = comp.find("]", i + 1)
            if j == -1:
                out.append("[")
            else:
                inner = comp[i + 1:j]
                if inner[:1] in ("!", "^") or not inner:
                    out.append("~")
                else:
                    out.append(inner[0])
                i = j
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _make_cases(policy, root):
    cases, listed, skipped = [], [], {}
    unlisted = set()

    def skip(name):
        skipped[name] = skipped.get(name, 0) + 1

    def write(rel, data, list_it=True):
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, data if isinstance(data, bytes) else data.encode("utf-8"))
        finally:
            os.close(fd)
        if list_it:
            listed.append(rel)
        return p

    def safe(rel):
        """Rename a non-deny case path until no deny pattern touches it."""
        cand = rel
        for k in range(6):
            if not deny_glob_hits(cand, policy.deny):
                return cand
            head, _sep, tail = cand.rpartition("/")
            cand = (head + "/ok%d" % k + "/" + tail) if head else ("ok%d/" % k + tail)
        return None

    def plant(cid, rel, content, cat, code, word, list_it=True):
        rel2 = safe(rel)
        if rel2 is None:
            skip("deny-collision")
            return
        write(rel2, content, list_it)
        cases.append(ControlCase(cid, rel2, "plant", cat, code, word))

    def decoy(cid, rel, content, cat, code, word, kind="decoy"):
        rel2 = safe(rel)
        if rel2 is None:
            skip("deny-collision")
            return
        write(rel2, content)
        cases.append(ControlCase(cid, rel2, kind, cat, code, word))

    for w in policy.words:
        t = w.text
        wl = w.id.lower()
        base = "w/%s-" % wl
        plant(w.id + "/plain", base + "plain.txt", "alpha " + t + " omega\n", "W", "W", w.id)
        if t.upper() != t:
            plant(w.id + "/upper", base + "upper.txt", "alpha " + t.upper() + " omega\n", "W", "W", w.id)
        else:
            skip("upper")
        nfd = unicodedata.normalize("NFD", t)
        if nfd != t:
            plant(w.id + "/nfd", base + "nfd.txt", "alpha " + nfd + " omega\n", "W", "W", w.id)
            plant(w.id + "/nfd-zwsp", base + "nfd-zwsp.txt", "alpha " + nfd[0] + "\u200b" + nfd[1:] + " omega\n",
                  "W", "W", w.id)
        else:
            skip("nfd")
        if any(c.isascii() and c.isalnum() for c in t):
            plant(w.id + "/fullwidth", base + "fullwidth.txt", "alpha " + _fullwidth(t) + " omega\n",
                  "W", "W", w.id)
        else:
            skip("fullwidth")
        if len(t) >= 2:
            plant(w.id + "/zwsp", base + "zwsp.txt", "alpha " + t[0] + "\u200b" + t[1:] + " omega\n",
                  "W", "W", w.id)
        else:
            skip("zwsp")
        plant(w.id + "/pct", base + "pct.txt", "link ?q=" + _pct(t) + " end\n", "W", "W", w.id)
        plant(w.id + "/html", base + "html.txt", "<p>" + _entities(t) + "</p>\n", "W", "W", w.id)
        plant(w.id + "/json", base + "json.json", '{"k": "' + _json_escape(t) + '"}\n', "W", "W", w.id)
        enc = _b64_variant(t)
        if enc:
            plant(w.id + "/b64", base + "b64.txt", "auth " + enc + " end\n", "W", "W", w.id)
        else:
            skip("b64")
        if "/" not in t and "\x00" not in t and len(t.encode("utf-8")) <= 200:
            plant(w.id + "/fname", "n/f-" + t + "-f.txt", "neutral line\n", "W", "W", w.id)
        else:
            skip("fname")
        if "/" in t:
            comps = [c for c in t.split("/") if c]
            if comps and not any(c in (".", "..") for c in comps):
                plant(w.id + "/pathlike", "p/%s/%s/f.txt" % (wl, "/".join(comps)), "neutral line\n",
                      "W", "W", w.id)
            else:
                skip("pathlike")
        if w.mode == "separated":
            parts = [p for p in _SEP_SPLIT.split(t) if p]
            if len(parts) >= 2:
                for nm, sep in (("sep-dash", "-"), ("sep-space", " "), ("sep-none", "")):
                    plant(w.id + "/" + nm, base + nm + ".txt", "alpha " + sep.join(parts) + " omega\n",
                          "W", "W", w.id)
            else:
                skip("sep")
        if w.mode == "bounded":
            plant(w.id + "/bounded-hit", base + "bounded-hit.txt",
                  "\ubaa9\ud45c " + t + "\ub144\n", "W", "W", w.id)
            decoy(w.id + "/bounded-decoy", base + "bounded-decoy.txt", "1" + t + "0\n", "W", "W", w.id)
        for ei, (en, _offs) in enumerate(w.exceptions, 1):
            decoy("%s/exc%d-decoy" % (w.id, ei), base + "exc%d-decoy.txt" % ei,
                  "lorem " + en + " ipsum\n", "W", "W", w.id)
            plant("%s/exc%d-near" % (w.id, ei), base + "exc%d-near.txt" % ei,
                  "lorem " + en + " " + t + " ipsum\n", "W", "W", w.id)

    user = "zq" + "-tester"
    homes = (
        ("home-posix", "path is " + "/" + "Users" + "/" + user + "/notes\n"),
        ("home-linux", "path is " + "/" + "home" + "/" + user + "/notes\n"),
        ("home-windows", "path is " + "C:" + "\\" + "Users" + "\\" + user + "\\notes\n"),
        ("home-encoded", "dir projects/-" + "Users" + "-" + "zq" + "tester" + "-repo\n"),
    )
    for nm, content in homes:
        plant("builtin/" + nm, "b/%s.txt" % nm, content, "H", "H-home", None)
    decoy("builtin/home-placeholder", "b/home-placeholder.txt",
          "see " + "/" + "Users" + "/<name>/x and " + "/" + "home" + "/you/x\n", "H", "H-home", None)
    plant("builtin/email", "b/email.txt",
          "write to " + "zq.canary" + "@" + "mail-canary.zz" + " soon\n", "E", "E-email", None)
    decoy("builtin/email-allowed", "b/email-allowed.txt",
          "write to " + "someone" + "@" + "example.com" + " soon\n", "E", "E-email", None)
    keys = (
        ("key-a", "sk-" + "ant-" + "A1b2" * 6),
        ("key-b", "sk-" + "Q9w8" * 6),
        ("key-c", "gh" + "p_" + "Z1y2" * 9),
        ("key-d", "AK" + "IA" + "M3N4" * 4),
        ("key-e", "xo" + "xb-" + "1234567890ab"),
        ("key-f", "AI" + "za" + "R5t6" * 8 + "abc"),
        ("key-g", "-----BEGIN " + "RSA PRIVATE" + " KEY-----"),
    )
    for nm, key in keys:
        plant("builtin/" + nm, "b/%s.txt" % nm, "token " + key + "\n", "K", "K-key", None)
    decoy("builtin/key-decoy", "b/key-decoy.txt", "the task-" + "abcdefghij" * 3 + "\n",
          "K", "K-key", None)
    seed = 0
    while True:
        data = (hashlib.sha256(("opaque-%d" % seed).encode()).digest()
                + hashlib.sha256(("opaque-%d-b" % seed).encode()).digest()[:28])
        enc = base64.b64encode(data).decode("ascii")
        if _is_opaque(enc.rstrip("=")):
            break
        seed += 1
    plant("builtin/opaque", "b/opaque.txt", "blob " + enc + " end\n", "B", "B-opaque", None)
    decoy("builtin/opaque-decoy-lower", "b/opaque-decoy-lower.txt", "path " + "docs/" * 12 + "\n",
          "B", "B-opaque", None)
    decoy("builtin/opaque-decoy-path", "b/opaque-decoy-path.txt",
          "path " + "Docs1/Part2/Sec3/" * 4 + "\n", "B", "B-opaque", None)

    # structure
    link = os.path.join(root, "s", "link")
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink("../../zq-outside-target", link)
    listed.append("s/link")
    cases.append(ControlCase("struct/symlink", "s/link", "plant", "S", "S-symlink", None))
    fifo = os.path.join(root, "s", "fifo")
    try:
        os.mkfifo(fifo)
        listed.append("s/fifo")
        cases.append(ControlCase("struct/fifo", "s/fifo", "plant", "S", "S-special", None))
    except (AttributeError, OSError):
        skip("fifo")
    write("s/hard-a.txt", "neutral\n")
    os.link(os.path.join(root, "s", "hard-a.txt"), os.path.join(root, "s", "hard-b.txt"))
    listed.append("s/hard-b.txt")
    cases.append(ControlCase("struct/hardlink", "s/hard-a.txt", "plant", "S", "S-hardlink", None))
    write("s/nul.bin", b"ab\x00cd\n")
    cases.append(ControlCase("struct/nul", "s/nul.bin", "plant", "S", "S-binary", None))
    write("s/latin.txt", b"caf\xe9\n")
    cases.append(ControlCase("struct/not-utf8", "s/latin.txt", "plant", "S", "S-binary", None))
    big = write("s/big.txt", b"")
    os.truncate(big, policy.max_file_bytes + 1)
    cases.append(ControlCase("struct/oversize", "s/big.txt", "plant", "S", "S-oversize", None))
    write("s/.DS_Store", b"\x00\x01")
    cases.append(ControlCase("struct/ds-store", "s/.DS_Store", "plant", "S", "S-junk", None))
    write("s/__pycache__/m.pyc", b"\x00", list_it=False)
    cases.append(ControlCase("struct/pycache", "s/__pycache__/m.pyc", "plant", "S", "S-junk", None))
    write("s/.env", "X=1\n")
    cases.append(ControlCase("struct/env", "s/.env", "plant", "S", "S-junk", None))
    write("s/repo/.git/HEAD", "ref: x\n", list_it=False)
    write("s/repo/keep.txt", "neutral\n")
    cases.append(ControlCase("struct/git", "s/repo/.git/HEAD", "plant", "S", "S-git", None))
    write("s/broken.json", "{oops\n")
    cases.append(ControlCase("struct/json", "s/broken.json", "plant", "S", "S-json-undecodable", None))

    # deny patterns
    for i, g in enumerate(policy.deny):
        sample = "/".join(_glob_sample(c) for c in g.comps)
        rel = sample + "/f.txt" if g.dir_only else sample
        if not g.anchored:
            rel = "d/%d/%s" % (i, rel)
        if g.raw not in deny_glob_hits(rel, policy.deny):
            skip("deny-sample")
            continue
        try:
            write(rel, "neutral\n")
        except OSError:
            skip("deny-sample")
            continue
        cases.append(ControlCase("deny/%d" % i, rel, "plant", "G", "G-deny", None))

    # manifest comparison
    write("m/listed.txt", "neutral\n")
    write("m/unlisted.txt", "neutral\n", list_it=False)
    unlisted.add("m/unlisted.txt")
    cases.append(ControlCase("manifest/unlisted", "m/unlisted.txt", "plant", "S", "S-unlisted", None))
    listed.append("m/phantom.txt")
    cases.append(ControlCase("manifest/missing", "m/phantom.txt", "plant", "S", "S-missing", None))
    decoy("clean", "c/clean.txt", "This line has nothing to flag.\n", None, None, None, kind="clean")
    return cases, listed, skipped


def _ancestors(rel):
    parts = rel.split("/")
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _evaluate(cases, res, skipped):
    by_path = {}
    for f in res.findings:
        by_path.setdefault(f.path, []).append(f)
    out = ControlResult(skipped=dict(skipped))
    for c in cases:
        if c.kind == "plant":
            out.planted += 1
            hit = False
            for p in _ancestors(c.relpath):
                for f in by_path.get(p, ()):
                    if f.cat == c.cat and (c.code is None or f.code == c.code) and (
                            c.word is None or f.word == c.word):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                out.caught += 1
            else:
                out.missed.append(c.case_id)
        else:
            out.decoys += 1
            fl = by_path.get(c.relpath, ())
            if c.kind == "clean":
                bad = bool(fl)
            else:
                bad = any(f.cat == c.cat and (c.word is None or f.word == c.word) for f in fl)
            if bad:
                out.decoys_flagged += 1
                out.missed.append(c.case_id + " (decoy flagged)")
    return out


def run_control(policy):
    """Plant every claimed variant in a private temp folder, scan it with the same
    scan() and check that everything planted was caught. Raises ToolError when the
    folder (which holds planted policy words) cannot be removed."""
    removed, problems = _stale_control_cleanup()
    if problems:
        raise ToolError("stale control folders could not be removed (they hold planted policy "
                        "words): %s" % ", ".join(problems))
    t0 = time.monotonic()
    base = tempfile.mkdtemp(prefix=CONTROL_PREFIX)
    result, error = None, None
    try:
        with open(os.path.join(base, CONTROL_MARKER), "w", encoding="utf-8") as fh:
            fh.write("%s\npid=%d\ncreated=%d\n" % (CONTROL_SIGNATURE, os.getpid(), int(time.time())))
        root = os.path.join(base, "tree")
        os.mkdir(root, 0o700)
        cases, listed, skipped = _make_cases(policy, root)
        res = scan(root, policy, listed, ScanOptions())
        result = _evaluate(cases, res, skipped)
    except BaseException as exc:
        error = exc
    removal_error = None
    try:
        _rmtree_control(base)
    except OSError as exc:
        removal_error = exc
    if removal_error is not None:
        raise ToolError("control folder %s could not be removed (%s); it holds planted policy "
                        "words - the next run removes it" % (base, removal_error.strerror or removal_error))
    if error is not None:
        if isinstance(error, OSError):
            raise ToolError("control set-up failed: %s" % (error.strerror or error))
        raise error
    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
    result.stale_removed = len(removed)
    return result


# ---------------------------------------------------------------- git metadata
def git_metadata_texts(root):
    git = shutil.which("git")
    if not git:
        raise ConfigError("--git-metadata needs the git program, which is not on PATH")
    if not os.path.isdir(os.path.join(root, ".git")):
        raise ConfigError("--git-metadata needs ROOT/.git")
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat", "LC_ALL": "C"})
    cmds = (
        ("log", [git, "-C", root, "-c", "core.quotepath=false", "-c", "log.showSignature=false",
                 "log", "--all", "--no-color",
                 "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%B%x1e"]),
        ("tags", [git, "-C", root, "for-each-ref", "refs/tags",
                  "--format=%(refname)%1f%(taggername)%1f%(taggeremail)%1f%(contents)%1e"]),
        ("config", [git, "-C", root, "config", "--local", "--list"]),
    )
    out = []
    for name, argv in cmds:
        try:
            cp = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=GIT_TIMEOUT_S, env=env)
        except subprocess.TimeoutExpired:
            raise ToolError("git %s timed out" % name)
        except OSError as exc:
            raise ToolError("git %s could not run: %s" % (name, exc))
        text = cp.stdout.decode("utf-8", "replace")
        if cp.returncode != 0:
            err = cp.stderr.decode("utf-8", "replace")
            if name == "log" and "does not have any commits" in err:
                text = ""
            elif name == "config" and cp.returncode == 1 and not text:
                text = ""
            else:
                raise ToolError("git %s failed with exit %d" % (name, cp.returncode))
        if name == "log":
            for rec in text.split("\x1e"):
                rec = rec.strip("\n")
                if not rec:
                    continue
                fields = rec.split("\x1f")
                out.append(("<git:commit %s>" % fields[0][:12], "\n".join(fields[1:])))
        elif name == "tags":
            for rec in text.split("\x1e"):
                rec = rec.strip("\n")
                if rec:
                    fields = rec.split("\x1f")
                    out.append(("<git:tag %s>" % fields[0].rsplit("/", 1)[-1], "\n".join(fields[1:])))
        else:
            out.append(("<git:config>", text))
    return out


def _scan_git_metadata(root, policy, res, show):
    sink = _Sink(policy, show)
    for vpath, text in git_metadata_texts(root):
        _detect(_Text(norm(text)), sink, vpath, "git-meta")
    res.views_used.add("git-meta")
    res.findings.extend(sink.ordered())
    for k, v in sink.suppressed.items():
        res.suppressed[k] = res.suppressed.get(k, 0) + v


# ---------------------------------------------------------------------- report
_KO = {
    EXIT_PASS: "\ud310\uc815: \ud1b5\uacfc - \ub2e8\uc5b4 \ud558\ud55c\ub9cc \ud1b5\uacfc. "
               "\uc758\ubbf8 \uac80\ud1a0\ub294 \ubcc4\ub3c4(\uc774 \ub3c4\uad6c\ub294 "
               "\uc758\ubbf8\ub97c \ubabb \ubcf8\ub2e4).",
    EXIT_FINDINGS: "\ud310\uc815: \uc801\ubc1c %d\uac74 - \uacf5\uac1c \ubd88\uac00. "
                   "\ubaa9\ub85d\uc744 \uace0\uce58\uace0 \ub2e4\uc2dc \ub3cc\ub824\ub77c.",
    EXIT_CONTROL_FAILED: "\ud310\uc815: \ub300\uc870\uad70 \uc2e4\ud328 - \ubcf8 \uac80\uc0ac\ub97c "
                         "\ud558\uc9c0 \uc54a\uc558\ub2e4. \uac80\uc0ac\uae30\ub098 \uc815\ucc45\uc774 "
                         "\uace0\uc7a5\uc774\ub2e4.",
    EXIT_CONFIG: "\ud310\uc815: \uc124\uc815 \uc624\ub958 - \uc544\ubb34\uac83\ub3c4 "
                 "\uac80\uc0ac\ud558\uc9c0 \uc54a\uc558\ub2e4.",
    EXIT_TOOL: "\ud310\uc815: \ub3c4\uad6c \uc624\ub958 - \ud1b5\uacfc\ub85c \uc77d\uc9c0 \ub9d0 \uac83.",
}


def emit_verdict(code, lang="en", reason="", count=0, caught=0, planted=0, digest=None,
                 control_only=False, out=None):
    out = out or sys.stdout
    if lang == "ko" and code in _KO:
        msg = _KO[code]
        if code == EXIT_FINDINGS:
            msg = msg % count
        print(msg, file=out)
    if code == EXIT_PASS:
        if control_only:
            line = "RESULT: CONTROL PASS (exit 0) - planted %d/%d caught; no tree was scanned." % (caught, planted)
        else:
            line = ("RESULT: PASS (exit 0) - word floor clear; semantic review still required "
                    "(this tool does not read meaning). digest=%s" % (digest or "-"))
    elif code == EXIT_FINDINGS:
        line = "RESULT: FINDINGS %d (exit 10) - do not publish; fix the listed items and re-run." % count
    elif code == EXIT_CONTROL_FAILED:
        line = ("RESULT: CONTROL FAILED %d/%d (exit 20) - ROOT was not scanned; the checker or the "
                "policy is broken." % (caught, planted))
    elif code == EXIT_CONFIG:
        line = "RESULT: CONFIG ERROR (exit 30) - %s; nothing was scanned." % reason
    elif code == EXIT_INTERRUPTED:
        line = "RESULT: INTERRUPTED (exit 130) - treat as NOT passed."
    else:
        line = ("RESULT: TOOL ERROR (exit 40) - %s; treat as NOT passed. findings so far: %d"
                % (reason, _PARTIAL["findings"]))
    print(line, file=out)
    out.flush()


def _display_path(path, policy, show):
    if show:
        return path
    if path.startswith("<git:"):
        return _mask_text(norm(path), policy)
    masked = _mask_text(norm("/" + path), policy)[1:]
    return masked if masked != norm(path) else path


def print_findings(findings, policy, show, out=None):
    out = out or sys.stdout
    for f in findings[:TERMINAL_FINDINGS_MAX]:
        where = _display_path(f.path, policy, show)
        if f.line:
            where = "%s:%d" % (where, f.line)
        extra = f.excerpt or f.detail
        if f.detail and f.detail not in extra and f.cat in ("W", "H", "E", "K"):
            extra = "%s  (at %s)" % (extra, f.detail)
        print("  %-18s %-4s %s  %s  [%s]" % (f.code, f.word or "-", where, extra, ", ".join(f.views)), file=out)
    if len(findings) > TERMINAL_FINDINGS_MAX:
        print("  ... %d more findings (see the JSON report)" % (len(findings) - TERMINAL_FINDINGS_MAX), file=out)


def _scope_line(res, policy, opts):
    views = ", ".join(sorted(res.views_used))
    not_checked = "meaning"
    if not opts.git_metadata:
        not_checked += ", git metadata"
    not_checked += ", text inside allow-listed binaries beyond plain byte strings"
    return ("SCOPE: scanned %d files (%d bytes) in %d folders; views used: %s; detectors: home-path, "
            "email, key-format, opaque(%d+), structure; policy: %d words, %d deny globs; NOT checked: %s.%s"
            % (res.files, res.bytes, res.dirs, views, B64_OPAQUE_MIN, len(policy.words), len(policy.deny),
               not_checked, (" " + " ".join(res.notes)) if res.notes else ""))


def _control_line(ctl):
    sk = ", ".join("%s %d" % kv for kv in sorted(ctl.skipped.items())) or "none"
    return ("CONTROL: planted %d, caught %d, decoys %d, decoys flagged %d, skipped variants: %s, "
            "%d ms%s" % (ctl.planted, ctl.caught, ctl.decoys, ctl.decoys_flagged, sk, ctl.elapsed_ms,
                         (", stale folders removed %d" % ctl.stale_removed) if ctl.stale_removed else ""))


def write_json_atomic(path, obj):
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=True, indent=1, sort_keys=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _realpath_inside(child, parent):
    c = os.path.realpath(child)
    p = os.path.realpath(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _check_root(root):
    try:
        st = os.lstat(root)
    except OSError:
        raise ConfigError("ROOT does not exist: %s" % root)
    if stat.S_ISLNK(st.st_mode):
        raise ConfigError("ROOT is a symbolic link (not followed): %s" % root)
    if not stat.S_ISDIR(st.st_mode):
        raise ConfigError("ROOT is not a folder: %s" % root)


def load_manifest_paths(path):
    """Read only what this tool needs from an export manifest: entry paths and
    their methods. Full validation belongs to export.py."""
    obj, sha = read_json_strict(path)
    if not isinstance(obj, dict) or obj.get("format") != MANIFEST_FORMAT:
        raise ConfigError("manifest: format must be %r" % MANIFEST_FORMAT)
    entries = obj.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ConfigError("manifest: entries must be a non-empty list")
    paths, methods = [], {}
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not isinstance(e.get("path"), str):
            raise ConfigError("manifest: entries[%d].path missing" % i)
        why = validate_relpath(e["path"])
        if why:
            raise ConfigError("manifest: entries[%d].path: %s" % (i, why))
        paths.append(e["path"])
        methods[e["path"]] = e.get("method", "?")
    return paths, methods, sha


def _config_obj(cli_path):
    p, src = find_config(cli_path)
    if p is None:
        return {}, None
    obj, _sha = read_json_strict(p)
    if not isinstance(obj, dict):
        raise ConfigError("config %s must be a JSON object" % p)
    return obj, p


def _policy_path_from(args_policy, cfg):
    if args_policy:
        return expand_user_path(args_policy)
    ex = cfg.get("export") if isinstance(cfg, dict) else None
    if isinstance(ex, dict) and isinstance(ex.get("policy"), str):
        return expand_user_path(ex["policy"])
    return None


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def cmd_scan(args):
    if args.show_matches:
        if args.json_report:
            raise ConfigError("--show-matches cannot be combined with --json-report "
                              "(raw words would be written to a file)")
        if not sys.stdout.isatty():
            raise ConfigError("--show-matches works only on an interactive terminal "
                              "(stdout is not a tty); it is for local diagnosis")
    cfg, _cfg_path = _config_obj(args.config)
    lang = args.lang or (cfg.get("lang") if cfg.get("lang") in ("en", "ko") else "en")
    args._lang = lang
    policy_path = _policy_path_from(args.policy, cfg)
    if not policy_path:
        raise ConfigError("no policy: pass --policy or set export.policy in the config "
                          "(a scan without a policy cannot pass)")
    root = args.root
    _check_root(root)
    policy = load_policy(policy_path)
    if _realpath_inside(policy_path, root):
        raise ConfigError("the policy file is inside ROOT - publishing it would leak the word list")
    manifest_paths = methods = manifest_sha = None
    if args.manifest:
        if _realpath_inside(args.manifest, root):
            raise ConfigError("the manifest is inside ROOT")
        manifest_paths, methods, manifest_sha = load_manifest_paths(args.manifest)
    if args.json_report and _realpath_inside(os.path.dirname(os.path.abspath(args.json_report)), root):
        raise ConfigError("--json-report must be written outside ROOT")
    if args.git_metadata and not args.allow_git_dir:
        raise ConfigError("--git-metadata needs --allow-git-dir (it reads ROOT/.git)")
    opts = ScanOptions(allow_git_dir=args.allow_git_dir, git_metadata=args.git_metadata,
                       show_matches=args.show_matches)
    started = _now_iso()
    t0 = time.monotonic()
    ctl = run_control(policy)
    report = {"format": REPORT_FORMAT, "tool_version": TOOL_VERSION,
              "python": sys.version.split()[0], "unicode": unicodedata.unidata_version,
              "root": os.path.abspath(root), "policy_sha256": policy.sha256,
              "manifest_sha256": manifest_sha, "started": started, "control": ctl.as_dict()}
    if not ctl.passed:
        print("CONTROL MISSED: " + ", ".join(ctl.missed[:50]))
        print(_control_line(ctl))
        report.update({"elapsed_ms": int((time.monotonic() - t0) * 1000), "findings_count": None,
                       "findings": [], "suppressed": {}, "tree_digest": None,
                       "exit": EXIT_CONTROL_FAILED, "verdict": "CONTROL_FAILED"})
        if args.json_report:
            write_json_atomic(args.json_report, report)
        emit_verdict(EXIT_CONTROL_FAILED, lang, caught=ctl.caught, planted=ctl.planted)
        return EXIT_CONTROL_FAILED
    _PARTIAL["findings"] = 0
    res = scan(root, policy, manifest_paths, opts, track_partial=True)
    if opts.git_metadata:
        _scan_git_metadata(root, policy, res, opts.show_matches)
    code = EXIT_FINDINGS if res.findings else EXIT_PASS
    print_findings(res.findings, policy, opts.show_matches)
    print(_scope_line(res, policy, opts))
    print(_control_line(ctl))
    if res.suppressed:
        print("SUPPRESSED (exception contexts): " + ", ".join(
            "%s x%d" % kv for kv in sorted(res.suppressed.items())))
    priority = None
    if methods is not None:
        priority = {"rewrite": sorted(p for p, m in methods.items() if m == "rewrite"),
                    "copy-sanitize": sorted(p for p, m in methods.items() if m == "copy-sanitize"),
                    "new": sorted(p for p, m in methods.items() if m == "new")}
        print("PRIORITY for semantic review: rewrite %d > copy-sanitize %d > new %d"
              % (len(priority["rewrite"]), len(priority["copy-sanitize"]), len(priority["new"])))
    digest = res.digest.value if res.digest else None
    print("DIGEST: %s" % (digest or "unavailable"))
    if args.json_report:
        report.update({"elapsed_ms": int((time.monotonic() - t0) * 1000),
                       "scope": {"files": res.files, "dirs": res.dirs, "bytes": res.bytes,
                                 "views": sorted(res.views_used),
                                 "detectors": ["home-path", "email", "key-format", "opaque", "structure"],
                                 "words": len(policy.words), "deny_globs": len(policy.deny),
                                 "notes": res.notes},
                       "findings_count": len(res.findings),
                       "findings": [dict(f.as_dict(), path=_display_path(f.path, policy, False))
                                    for f in res.findings],
                       "suppressed": res.suppressed, "priority": priority,
                       "tree_digest": digest, "exit": code,
                       "verdict": "PASS" if code == EXIT_PASS else "FINDINGS"})
        try:
            write_json_atomic(args.json_report, report)
        except OSError as exc:
            raise ToolError("could not write the JSON report: %s" % (exc.strerror or exc))
    emit_verdict(code, lang, count=len(res.findings), digest=digest)
    return code


def cmd_control(args):
    cfg, _p = _config_obj(args.config)
    lang = args.lang or (cfg.get("lang") if cfg.get("lang") in ("en", "ko") else "en")
    args._lang = lang
    policy_path = _policy_path_from(args.policy, cfg)
    if not policy_path:
        raise ConfigError("no policy: pass --policy or set export.policy in the config")
    policy = load_policy(policy_path)
    ctl = run_control(policy)
    if not ctl.passed:
        print("CONTROL MISSED: " + ", ".join(ctl.missed[:50]))
    print(_control_line(ctl))
    code = EXIT_PASS if ctl.passed else EXIT_CONTROL_FAILED
    emit_verdict(code, lang, caught=ctl.caught, planted=ctl.planted, control_only=True)
    return code


def cmd_digest(args):
    _check_root(args.root)
    d = tree_digest(args.root, args.allow_git_dir)
    print("%s  (%d items)" % (d.value, len(d.files)))
    return EXIT_PASS


# --------------------------------------------------------------------- selftest
def _limitation_exhibits(word):
    """Inputs the docstring says are NOT caught (L3/L4). If one starts being
    caught, the docstring is wrong and selftest fails."""
    return (
        ("homoglyph", word[:2] + "c" + "\u0430" + word[4:] if len(word) > 4 else word + "\u0430"),
        ("reversed", word[::-1]),
        ("split-line", word[:4] + "\n" + word[4:]),
        ("hex", word.encode("utf-8").hex()),
        ("py-escape", "".join("\\u%04x" % ord(c) for c in word)),
        ("short-base64", base64.b64encode(word.encode("utf-8")).decode("ascii")),
    )


def selftest(policy_path=None, out=None):
    out = out or sys.stdout
    failures = []
    syn = policy_from_obj(example_policy(), "", "<synthetic>")
    ctl = run_control(syn)
    print("selftest 1/4 synthetic control: planted %d, caught %d, decoys %d flagged %d"
          % (ctl.planted, ctl.caught, ctl.decoys, ctl.decoys_flagged), file=out)
    if not ctl.passed:
        failures.append("synthetic control missed: " + ", ".join(ctl.missed))
    canary = syn.words[0].text
    tmp = tempfile.mkdtemp(prefix="leakcheck-selftest-")
    try:
        names = {}
        for name, content in _limitation_exhibits(canary):
            fn = "x.py" if name == "py-escape" else name + ".txt"
            with open(os.path.join(tmp, fn), "w", encoding="utf-8") as fh:
                fh.write("value " + content + " end\n")
            names[fn] = name
        res = scan(tmp, syn, None, ScanOptions())
        caught = sorted(names[f.path] for f in res.findings if f.word == syn.words[0].id and f.path in names)
        print("selftest 2/4 documented limits still not caught: %d/%d"
              % (len(names) - len(caught), len(names)), file=out)
        if caught:
            failures.append("documented limitation is now caught (update L3/L4): " + ", ".join(caught))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="leakcheck-selftest-")
    try:
        with open(os.path.abspath(__file__), "rb") as src, \
                open(os.path.join(tmp, "leak_check.py"), "wb") as dst:
            dst.write(src.read())
        res = scan(tmp, syn, None, ScanOptions())
        print("selftest 3/4 own source vs synthetic policy + built-in detectors: %d findings"
              % len(res.findings), file=out)
        if res.findings:
            failures.append("own source has findings: " + ", ".join(
                "%s:%d %s" % (f.path, f.line, f.code) for f in res.findings[:10]))
        if policy_path:
            real = load_policy(policy_path)
            rctl = run_control(real)
            rres = scan(tmp, real, None, ScanOptions())
            print("selftest 4/4 real policy: control planted %d caught %d flagged %d; own source "
                  "findings %d" % (rctl.planted, rctl.caught, rctl.decoys_flagged, len(rres.findings)), file=out)
            if not rctl.passed:
                failures.append("real-policy control missed: " + ", ".join(rctl.missed))
            if rres.findings:
                failures.append("own source has real-policy findings: %d" % len(rres.findings))
        else:
            print("selftest 4/4 real policy: skipped (no --policy and no export.policy in config)", file=out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        for f in failures:
            print("SELFTEST FAIL: " + f, file=out)
        print("RESULT: SELFTEST FAILED (exit 20) - do not trust this checker until fixed.", file=out)
        return EXIT_CONTROL_FAILED
    print("RESULT: SELFTEST PASS (exit 0) - claimed capabilities hold; documented limits unchanged.", file=out)
    return EXIT_PASS


# -------------------------------------------------------------------------- cli
class _ArgParser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigError("arguments: " + message)


def build_parser():
    p = _ArgParser(prog="leak_check.py",
                   description="Fail-closed word-floor scanner (read the module docstring for limits).")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("scan", help="control run, then scan ROOT")
    s.add_argument("root")
    s.add_argument("--policy")
    s.add_argument("--config")
    s.add_argument("--manifest")
    s.add_argument("--json-report")
    s.add_argument("--show-matches", action="store_true")
    s.add_argument("--allow-git-dir", action="store_true")
    s.add_argument("--git-metadata", action="store_true")
    s.add_argument("--lang", choices=("en", "ko"))
    s.add_argument("--debug", action="store_true")
    c = sub.add_parser("control", help="run the control set only")
    c.add_argument("--policy")
    c.add_argument("--config")
    c.add_argument("--lang", choices=("en", "ko"))
    c.add_argument("--debug", action="store_true")
    d = sub.add_parser("digest", help="print the tree digest")
    d.add_argument("root")
    d.add_argument("--allow-git-dir", action="store_true")
    d.add_argument("--debug", action="store_true")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--version"]:
        print("leak_check %s (api %d)" % (TOOL_VERSION, API_VERSION))
        return EXIT_PASS
    if "--selftest" in argv:
        sp = _ArgParser(prog="leak_check.py --selftest")
        sp.add_argument("--selftest", action="store_true")
        sp.add_argument("--policy")
        sp.add_argument("--config")
        sp.add_argument("--debug", action="store_true")
        a = sp.parse_args(argv)
        policy_path = a.policy
        if not policy_path:
            cfg, _p = _config_obj(a.config)
            policy_path = _policy_path_from(None, cfg)
        return selftest(policy_path)
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        raise ConfigError("missing command (scan, control, digest, --selftest or --version)")
    if sys.platform.startswith("win") or os.name != "posix":
        raise ConfigError("only macOS and Linux are supported")
    if args.cmd == "scan":
        return cmd_scan(args)
    if args.cmd == "control":
        return cmd_control(args)
    return cmd_digest(args)


def _setup_io():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


def _entry():
    _setup_io()
    argv = sys.argv[1:]
    debug = "--debug" in argv
    lang = "ko" if ("--lang" in argv and argv[argv.index("--lang") + 1:argv.index("--lang") + 2] == ["ko"]) else "en"
    if sys.version_info < (3, 9):
        print("RESULT: CONFIG ERROR (exit 30) - Python 3.9 or newer is required; nothing was scanned.")
        sys.exit(EXIT_CONFIG)
    try:
        code = main(argv)
    except KeyboardInterrupt:
        emit_verdict(EXIT_INTERRUPTED, lang)
        code = EXIT_INTERRUPTED
    except ConfigError as exc:
        emit_verdict(EXIT_CONFIG, lang, reason=str(exc))
        code = EXIT_CONFIG
    except ToolError as exc:
        if debug:
            traceback.print_exc()
        emit_verdict(EXIT_TOOL, lang, reason=str(exc))
        code = EXIT_TOOL
    except SystemExit:
        raise
    except BaseException as exc:
        if debug:
            traceback.print_exc()
        emit_verdict(EXIT_TOOL, lang, reason="%s: %s" % (type(exc).__name__, exc))
        code = EXIT_TOOL
    sys.exit(code)


if __name__ == "__main__":
    _entry()
