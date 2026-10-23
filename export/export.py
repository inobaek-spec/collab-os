#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""export - copy an allow-listed set of files into a release tree, and refuse to
promote it unless leak_check passes.

READ THE LIMITS FIRST. A gate that hides its limits gets read as "pass = safe".
================================================================================
E1. A pass means: only manifest-listed files are in the release tree, every
    copy-sanitize rule matched exactly the expected number of times, and
    leak_check returned PASS. It does NOT mean the content is safe to publish:
    leak_check does not read meaning. Semantic review is a separate gate.
E2. Sanitize rules are literal find/replace. They cannot rewrite meaning; text the
    rules do not cover goes out unless leak_check flags it.
E3. rewrite/new files are only checked for existence and copied as they are. This
    tool never opens the private file they were derived from, so it cannot tell
    whether one was pasted in verbatim.
E4. Private source files that are not in the manifest are never opened, listed or
    stat()-ed. The operating system still walks the parent folders of listed
    files while resolving their paths.
E5. Promotion relies on rename() within one volume, and the receipt replace is
    the single commit point. If the process is killed before that commit, the
    release folder may be missing or hold the new, uncommitted tree until the
    next build rolls it back; `verify` refuses an uncommitted tree. No fsync:
    after a power cut, run it again.
E6. Concurrent runs are blocked with flock(); on file systems without working
    flock (some network shares) two runs can collide.
E7. macOS and Linux only (fcntl, O_NOFOLLOW). Python 3.9 or newer.
E8. What happens after promotion (copying the tree into a public git clone, the
    commit author identity) is outside this tool. Run `verify --clone` before
    you push.
E9. Symlinks in a path are checked before opening, but a folder swapped for a
    link between that check and the open is not prevented (O_NOFOLLOW guards
    the last path component only). Assumes a single-user machine.
E10. The version check compares only the "version" string of the first
    manifest entry ending in .claude-plugin/plugin.json with the value the
    previous receipt recorded. It does not check that the marketplace entry,
    the plugin README or the hook comment carry the same version, it cannot
    tell whether the change deserved a bump, and a receipt written before this
    check existed has no plugin_version (reported as "cannot compare"). By
    default a same-version content change is only a WARNING; with
    --require-version-bump it is a CONFIG_ERROR (30) and dist is kept.

Exit codes
----------
  0   PROMOTED or UNCHANGED   release tree updated, or already identical
  10  FINDINGS                leak_check found something; release tree kept
  20  CONTROL_FAILED          leak_check control run failed; release tree kept
  30  CONFIG_ERROR            bad config, manifest or inputs; nothing copied
  40  TOOL_ERROR              unexpected error; release tree kept
  50  ROLLBACK_FAILED         promotion broke and could not be undone; the
                              printed state must be fixed by hand
  Any non-zero code except 50 leaves the release tree exactly as it was.
  Anything else (killed, crashed) means NOT committed; the next build rolls
  back an uncommitted promotion.

Usage
-----
  export.py check  [--config FILE]
  export.py build  [--config FILE] [--keep-failed] [--require-version-bump]
  export.py verify [--config FILE] [--expect sha256:HEX] [--clone PATH] [--git-metadata]
  export.py --selftest
  export.py --print-example {manifest,policy,config}
"""
import sys

# Before loading leak_check: never write __pycache__ (it embeds absolute paths)
# into the authored tree or the release tree.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import dataclasses  # noqa: E402
import datetime  # noqa: E402
import errno  # noqa: E402
import fcntl  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import traceback  # noqa: E402
import unicodedata  # noqa: E402

TOOL_VERSION = "1.0.0"
REQUIRED_LEAK_API = 1

EXIT_OK = 0
EXIT_FINDINGS = 10
EXIT_CONTROL_FAILED = 20
EXIT_CONFIG = 30
EXIT_TOOL = 40
EXIT_ROLLBACK = 50
EXIT_INTERRUPTED = 130

MANIFEST_FORMAT = "collab-os-export-manifest/1"
RECEIPT_FORMAT = "collab-os-export-receipt/1"
RUN_FORMAT = "collab-os-export-run/1"
EXPORT_KEYS = ("source_root", "authored_root", "out_dir", "manifest", "policy")
LEAK_TIMEOUT_S = 300
READ_CAP = 64 * 1024 * 1024

HERE = os.path.dirname(os.path.realpath(__file__))
LC_PATH = os.path.join(HERE, "leak_check.py")

_METHODS = ("copy-sanitize", "rewrite", "new")
_TOP_KEYS = frozenset(("format", "substitutions", "entries"))
_ENTRY_KEYS = frozenset(("path", "method", "source", "apply", "basis", "executable", "note"))
_SUB_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_EXPECT_RX = re.compile(r"^sha256:[0-9a-f]{64}$")


class ConfigError(Exception):
    """Bad config, manifest or inputs. Exit 30."""


class ToolError(Exception):
    """Runtime failure; the release tree is unchanged. Exit 40."""


class InvariantError(ToolError):
    """An internal rule was broken (a bug). Exit 40."""


class RollbackFailed(Exception):
    """Promotion failed and could not be undone. Exit 50."""


_LC = None
_STATE = {"lang": "en", "debug": False}


def load_leak_check():
    """Load the sibling leak_check.py by its exact path (no sys.path search)."""
    global _LC
    if _LC is not None:
        return _LC
    if not os.path.isfile(LC_PATH):
        raise ToolError("leak_check.py not found next to export.py (%s)" % LC_PATH)
    name = "collab_os_leak_check"
    spec = importlib.util.spec_from_file_location(name, LC_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise ToolError("leak_check.py could not be loaded: %s" % exc)
    if getattr(mod, "API_VERSION", None) != REQUIRED_LEAK_API:
        raise ToolError("leak_check.py API %r, export needs %d" % (getattr(mod, "API_VERSION", None),
                                                               REQUIRED_LEAK_API))
    _LC = mod
    return mod


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _new_run_id():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)


def _warn(msg):
    print("WARNING: " + msg)


def _real_dir(p):
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode)


def _inside(child, parent):
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


# ---------------------------------------------------------------------- config
@dataclasses.dataclass(frozen=True)
class ExportConfig:
    config_path: str
    config_source: str
    config_sha256: str
    lang: str
    source_root: str
    authored_root: str
    out_dir: str
    meta_dir: str
    manifest_path: str
    policy_path: str


def _abs_user(lc, value, key):
    if not isinstance(value, str) or not value:
        raise ConfigError("export.%s must be a non-empty string" % key)
    v = lc.expand_user_path(value)
    if not os.path.isabs(v):
        raise ConfigError("export.%s must be absolute or start with ~/ (got %r)" % (key, value))
    return os.path.normpath(v)


def _require_dir(p, what):
    try:
        st = os.lstat(p)
    except OSError:
        raise ConfigError("%s does not exist: %s" % (what, p))
    if stat.S_ISLNK(st.st_mode):
        raise ConfigError("%s is a symbolic link (not followed): %s" % (what, p))
    if not stat.S_ISDIR(st.st_mode):
        raise ConfigError("%s is not a folder: %s" % (what, p))


def load_export_config(lc, cli_path):
    path, source = lc.find_config(cli_path)
    if path is None:
        raise ConfigError("no config file found (--config, %s, ~/.config/collab-os/config.json); "
                          "export never runs on built-in defaults" % lc.CONFIG_ENV)
    obj, sha = lc.read_json_strict(path)
    if not isinstance(obj, dict):
        raise ConfigError("%s must be a JSON object" % path)
    ex = obj.get("export")
    if not isinstance(ex, dict):
        raise ConfigError("%s has no \"export\" section; export never runs on built-in defaults" % path)
    unknown = sorted(set(ex) - set(EXPORT_KEYS))
    missing = [k for k in EXPORT_KEYS if k not in ex]
    if unknown or missing:
        raise ConfigError("export section: %s%s" % (
            ("unknown keys " + ", ".join(unknown)) if unknown else "",
            ((" ; " if unknown else "") + "missing keys " + ", ".join(missing)) if missing else ""))
    lang = obj.get("lang", "en")
    lang = lang if lang in ("en", "ko") else "en"
    src = _abs_user(lc, ex["source_root"], "source_root")
    auth = _abs_user(lc, ex["authored_root"], "authored_root")
    out = _abs_user(lc, ex["out_dir"], "out_dir")
    man = _abs_user(lc, ex["manifest"], "manifest")
    pol = _abs_user(lc, ex["policy"], "policy")
    _require_dir(src, "export.source_root")
    _require_dir(auth, "export.authored_root")
    parent = os.path.dirname(out)
    _require_dir(parent, "the parent folder of export.out_dir")
    if os.path.lexists(out):
        _require_dir(out, "export.out_dir")
        if os.path.lexists(os.path.join(out, ".git")):
            raise ConfigError("export.out_dir contains .git - the release tree must not be a git clone "
                              "(promotion renames and deletes it); keep the public clone elsewhere")
    meta = os.path.join(parent, os.path.basename(out) + "-meta")
    if os.path.lexists(meta):
        _require_dir(meta, "the meta folder")
    src_r = os.path.realpath(src)
    auth_r = os.path.realpath(auth)
    parent_r = os.path.realpath(parent)
    out_r = os.path.join(parent_r, os.path.basename(out))
    meta_r = os.path.join(parent_r, os.path.basename(meta))
    roots = (("source_root", src_r), ("authored_root", auth_r), ("out_dir", out_r), ("meta folder", meta_r))
    for i, (na, a) in enumerate(roots):
        for nb, b in roots[i + 1:]:
            if _inside(a, b) or _inside(b, a):
                raise ConfigError("%s and %s overlap (%s, %s); they must be separate folders" % (na, nb, a, b))
    for what, f in (("export.manifest", man), ("export.policy", pol)):
        if not os.path.isfile(f):
            raise ConfigError("%s not found: %s" % (what, f))
        fr = os.path.realpath(f)
        for na, a in (("authored_root", auth_r), ("out_dir", out_r), ("meta folder", meta_r)):
            if _inside(fr, a):
                raise ConfigError("%s is inside %s - private data must stay outside the published "
                                  "trees" % (what, na))
    return ExportConfig(path, source, sha, lang, src_r, auth_r, out_r, meta_r, man, pol)


# -------------------------------------------------------------------- manifest
@dataclasses.dataclass(frozen=True)
class Rule:
    find: str
    replace: str
    count: object
    origin: str


@dataclasses.dataclass(frozen=True)
class Entry:
    path: str
    method: str
    source: object
    rules: tuple
    basis: object
    executable: bool


@dataclasses.dataclass(frozen=True)
class Manifest:
    entries: tuple
    sha256: str
    sources: frozenset
    path: str


def _parse_rules(apply, subs, where, errors, used):
    if not isinstance(apply, list):
        errors.append("%s.apply must be a list" % where)
        return ()
    rules = []
    for k, r in enumerate(apply, 1):
        rw = "%s.apply[%d]" % (where, k)
        if not isinstance(r, dict):
            errors.append("%s must be an object" % rw)
            continue
        count = r.get("count")
        if not ((isinstance(count, int) and not isinstance(count, bool) and count >= 0) or count == "*"):
            errors.append("%s.count must be an integer >= 0 or \"*\"" % rw)
            continue
        if "sub" in r:
            extra = sorted(set(r) - {"sub", "count"})
            if extra:
                errors.append("%s: unknown keys %s" % (rw, ", ".join(extra)))
                continue
            name = r["sub"]
            if name not in subs:
                errors.append("%s: no substitution named %r" % (rw, name))
                continue
            used.add(name)
            find, replace = subs[name]
            origin = name
        else:
            extra = sorted(set(r) - {"find", "replace", "count"})
            if extra:
                errors.append("%s: unknown keys %s" % (rw, ", ".join(extra)))
                continue
            find = r.get("find")
            replace = r.get("replace", "" if count == 0 else None)
            if not isinstance(find, str) or not find:
                errors.append("%s.find must be a non-empty string" % rw)
                continue
            if not isinstance(replace, str):
                errors.append("%s.replace must be a string" % rw)
                continue
            origin = "inline#%d" % k
        if count != 0 and find in replace:
            errors.append("%s (%s): the find text is part of its replacement - the text would remain" % (rw, origin))
            continue
        rules.append(Rule(find, replace, count, origin))
    return tuple(rules)


def load_manifest(lc, path, policy):
    obj, sha = lc.read_json_strict(path)
    if not isinstance(obj, dict):
        raise ConfigError("manifest must be a JSON object")
    errors, warnings = [], []
    unknown = sorted(set(obj) - _TOP_KEYS)
    if unknown:
        errors.append("unknown top-level keys %s" % ", ".join(unknown))
    if obj.get("format") != MANIFEST_FORMAT:
        errors.append("format must be %r" % MANIFEST_FORMAT)
    subs_in = obj.get("substitutions", {})
    subs = {}
    if not isinstance(subs_in, dict):
        errors.append("substitutions must be an object")
        subs_in = {}
    for name, d in subs_in.items():
        if not _SUB_NAME.match(name):
            errors.append("substitution name %r is not allowed" % name)
            continue
        if not isinstance(d, dict) or set(d) != {"find", "replace"}:
            errors.append("substitution %s must be {\"find\", \"replace\"}" % name)
            continue
        if not isinstance(d["find"], str) or not d["find"] or not isinstance(d["replace"], str):
            errors.append("substitution %s: find must be a non-empty string, replace a string" % name)
            continue
        subs[name] = (d["find"], d["replace"])
    entries_in = obj.get("entries")
    if not isinstance(entries_in, list) or not entries_in:
        errors.append("entries must be a non-empty list")
        entries_in = []
    used, entries, folded = set(), [], {}
    for i, e in enumerate(entries_in):
        where = "entries[%d]" % i
        if not isinstance(e, dict):
            errors.append("%s must be an object" % where)
            continue
        extra = sorted(set(e) - _ENTRY_KEYS)
        if extra:
            errors.append("%s: unknown keys %s" % (where, ", ".join(extra)))
        p = e.get("path")
        why = lc.validate_relpath(p) if isinstance(p, str) else "missing or not a string"
        if why:
            errors.append("%s.path: %s" % (where, why))
            continue
        f = lc.fold_path(p)
        if f in folded:
            errors.append("%s.path %r collides with %r (same name on a case-insensitive or "
                          "normalizing file system)" % (where, p, folded[f]))
            continue
        folded[f] = p
        method = e.get("method")
        if method not in _METHODS:
            errors.append("%s.method must be one of %s" % (where, ", ".join(_METHODS)))
            continue
        source, basis = e.get("source"), e.get("basis")
        rules = ()
        if method == "copy-sanitize":
            sw = lc.validate_relpath(source) if isinstance(source, str) else "missing or not a string"
            if sw:
                errors.append("%s.source: %s" % (where, sw))
                continue
            if basis is not None:
                errors.append("%s: basis is only for rewrite entries" % where)
            rules = _parse_rules(e.get("apply", []), subs, where, errors, used)
            for g in lc.deny_glob_hits(source, policy.deny):
                errors.append("%s.source %r matches deny pattern %r - it must not be exported" % (where, source, g))
        else:
            if "source" in e or "apply" in e:
                errors.append("%s: source/apply are only for copy-sanitize entries (rewrite/new "
                              "files are never read from the private source)" % where)
            if basis is not None and (method != "rewrite" or not isinstance(basis, str)):
                errors.append("%s: basis must be a string on rewrite entries only" % where)
            source = None
        executable = e.get("executable", False)
        if not isinstance(executable, bool):
            errors.append("%s.executable must be true or false" % where)
            executable = False
        note = e.get("note")
        if note is not None and not isinstance(note, str):
            errors.append("%s.note must be a string" % where)
        for g in lc.deny_glob_hits(p, policy.deny):
            errors.append("%s.path %r matches deny pattern %r" % (where, p, g))
        entries.append(Entry(p, method, source, rules, basis, executable))
    fset = set(folded)
    for f in sorted(fset):
        parts = f.split("/")
        for k in range(1, len(parts)):
            anc = "/".join(parts[:k])
            if anc in fset:
                errors.append("entry %r is a file and also a parent folder of %r" % (folded[anc], folded[f]))
    for name in sorted(set(subs) - used):
        warnings.append("substitution %s is never used" % name)
    if errors:
        raise ConfigError("manifest %s has %d problem(s):\n  - %s" % (path, len(errors), "\n  - ".join(errors)))
    entries.sort(key=lambda x: (lc.fold_path(x.path), x.path))
    sources = frozenset(x.source for x in entries if x.method == "copy-sanitize")
    return Manifest(tuple(entries), sha, sources, path), warnings


# ------------------------------------------------------------- source / inputs
class SourceReader:
    """The only way this tool touches the private source tree: listed files only."""

    def __init__(self, root, allowed):
        self._root = root
        self._allowed = frozenset(allowed)

    def _path(self, rel):
        if rel not in self._allowed:
            raise InvariantError("internal error: source %r is not in the allow-list" % rel)
        return os.path.join(self._root, *rel.split("/"))

    def lstat(self, rel):
        return os.lstat(self._path(rel))

    def read(self, rel, cap=READ_CAP):
        p = self._path(rel)
        parent = os.path.dirname(p)
        if os.path.realpath(parent) != os.path.normpath(parent):
            raise ConfigError("source %r: a folder on its path is a symbolic link (not followed)" % rel)
        return _read_file_nofollow(p, cap, "source %r" % rel)


def _read_file_nofollow(p, cap, what):
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfigError("%s is a symbolic link (not followed)" % what)
        raise ConfigError("%s cannot be opened: %s" % (what, exc.strerror or exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ConfigError("%s is not a regular file" % what)
        chunks, total = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
            total += len(b)
            if total > cap:
                raise ConfigError("%s is larger than %d bytes" % (what, cap))
        return b"".join(chunks)
    finally:
        os.close(fd)


def _authored_path(cfg, rel):
    return os.path.join(cfg.authored_root, *rel.split("/"))


def check_inputs(cfg, manifest, reader, bad_sources=None):
    """Existence checks only (no file content is read). Returns problem strings;
    sources with problems are added to bad_sources when given."""
    problems = []
    for e in manifest.entries:
        ap = _authored_path(cfg, e.path)
        if e.method in ("new", "rewrite"):
            try:
                st = os.lstat(ap)
            except FileNotFoundError:
                problems.append("missing authored file (%s): %s" % (e.method, e.path))
                continue
            except OSError as exc:
                problems.append("cannot stat authored file %s: %s" % (e.path, exc.strerror or exc))
                continue
            if stat.S_ISLNK(st.st_mode):
                problems.append("authored file is a symbolic link (not followed): %s" % e.path)
            elif not stat.S_ISREG(st.st_mode):
                problems.append("authored path is not a regular file: %s" % e.path)
            elif os.path.realpath(os.path.dirname(ap)) != os.path.normpath(os.path.dirname(ap)):
                problems.append("a folder on the path of %s is a symbolic link (not followed)" % e.path)
            elif not os.access(ap, os.R_OK):
                problems.append("authored file is not readable: %s" % e.path)
        else:
            if os.path.lexists(ap):
                problems.append("the authored tree also has %s, which is a copy-sanitize entry - "
                                "decide which one ships" % e.path)
            n_before = len(problems)
            try:
                st = reader.lstat(e.source)
            except FileNotFoundError:
                problems.append("missing source for %s: %s" % (e.path, e.source))
                st = None
            except OSError as exc:
                problems.append("cannot stat source %s: %s" % (e.source, exc.strerror or exc))
                st = None
            if st is not None:
                if stat.S_ISLNK(st.st_mode):
                    problems.append("source is a symbolic link (not followed): %s" % e.source)
                elif not stat.S_ISREG(st.st_mode):
                    problems.append("source is not a regular file: %s" % e.source)
            if bad_sources is not None and len(problems) > n_before:
                bad_sources.add(e.source)
    return problems


def unlisted_authored(lc, cfg, manifest):
    listed = {lc.fold_path(e.path) for e in manifest.entries}
    out = []
    for dp, dns, fns in os.walk(cfg.authored_root):
        dns[:] = sorted(d for d in dns if not os.path.islink(os.path.join(dp, d)))
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), cfg.authored_root).replace(os.sep, "/")
            if lc.fold_path(rel) not in listed:
                out.append(rel)
    return out


# --------------------------------------------------------------------- sanitize
def _pairs_hook(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("duplicate key %r" % (k,))
        out[k] = v
    return out


def post_validate(path, text):
    low = path.casefold()
    if low.endswith(".json"):
        try:
            json.loads(text.lstrip("﻿"), object_pairs_hook=_pairs_hook)
        except ValueError as exc:
            raise ConfigError("the sanitized %s is not valid JSON: %s" % (path, exc))
    elif low.endswith(".py"):
        try:
            compile(text, path, "exec", dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            raise ConfigError("the sanitized %s is not valid Python: %s" % (path, exc))


def sanitize(entry, raw):
    """Apply the entry's literal rules with exact counts. With no rules the
    original bytes are returned unchanged (after the UTF-8 and syntax checks)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("the source of %s is not UTF-8 (sanitize works on text)" % entry.path)
    if not entry.rules:
        post_validate(entry.path, text)
        return raw
    for r in entry.rules:
        n = text.count(r.find)
        if r.count != "*" and n != r.count:
            raise ConfigError("sanitize rule %s on %s expected %s match(es) but found %d - the source "
                              "changed; update the rule" % (r.origin, entry.path, r.count, n))
        if r.count != 0:
            text = text.replace(r.find, r.replace)
    post_validate(entry.path, text)
    return text.encode("utf-8")


# ------------------------------------------------------------ lock and receipt
def acquire_lock(meta_dir, shared=False, create=True):
    if create:
        os.makedirs(meta_dir, mode=0o700, exist_ok=True)
    try:
        fd = os.open(os.path.join(meta_dir, "lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise ToolError("cannot open the lock file in %s: %s" % (meta_dir, exc.strerror or exc))
    try:
        fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            raise ToolError("another export run holds the lock (%s)" % meta_dir)
        raise ToolError("cannot lock %s: %s" % (meta_dir, exc.strerror or exc))
    return fd


def _receipt_path(cfg):
    return os.path.join(cfg.meta_dir, "receipt.json")


def _tmp_receipt_path(cfg):
    return os.path.join(cfg.meta_dir, "receipt.json.tmp")


def _read_json_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_receipt(cfg):
    p = _receipt_path(cfg)
    if not os.path.isfile(p):
        return None
    try:
        rec = _read_json_file(p)
    except (OSError, ValueError) as exc:
        raise ConfigError("the receipt %s cannot be read: %s" % (p, exc))
    if not isinstance(rec, dict) or rec.get("format") != RECEIPT_FORMAT or "tree_digest" not in rec:
        raise ConfigError("the receipt %s is not a %s file" % (p, RECEIPT_FORMAT))
    return rec


def _write_json_file(path, obj):
    data = (json.dumps(obj, ensure_ascii=True, indent=1) + "\n").encode("ascii")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC, 0o600)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)


def _write_json_atomic(path, obj):
    part = "%s.%d.part" % (path, os.getpid())
    _write_json_file(part, obj)
    os.replace(part, path)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]


def recover(cfg, lc):
    """Undo or finish what an interrupted run left behind. Commit point = P4
    (receipt replace): anything before it is rolled back to the previous release."""
    meta, out = cfg.meta_dir, cfg.out_dir
    actions = []
    if not os.path.isdir(meta):
        return actions
    tmp = _tmp_receipt_path(cfg)
    if os.path.lexists(tmp):
        try:
            t = _read_json_file(tmp)
            run = t["run_id"]
            tdig = t["tree_digest"]
            if not isinstance(run, str) or not re.match(r"^[0-9A-Za-z-]+$", run):
                raise ValueError("bad run_id")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RollbackFailed("receipt.json.tmp is unreadable (%s) - cannot tell how an earlier "
                                 "promotion ended; inspect %s by hand" % (exc, meta))
        old_run = os.path.join(meta, "old-" + run)
        has_old = _real_dir(old_run)
        out_exists = os.path.lexists(out)
        out_dig = lc.tree_digest(out).value if out_exists else None
        try:
            if out_exists and out_dig == tdig:
                rb = os.path.join(meta, "rollback-" + run)
                os.rename(out, rb)
                if has_old:
                    os.rename(old_run, out)
                    actions.append("U1: rolled back the uncommitted promotion %s (previous release restored)" % run)
                else:
                    actions.append("U1: rolled back the uncommitted first promotion %s (no release tree now)" % run)
                shutil.rmtree(rb)
            elif not out_exists and has_old:
                os.rename(old_run, out)
                actions.append("U2: restored the previous release after the interrupted promotion %s" % run)
            elif out_exists and not has_old:
                actions.append("U3: dropped the receipt draft of %s (written before the release moved)" % run)
            elif not out_exists and not has_old:
                actions.append("U4: dropped the receipt draft of the interrupted first promotion %s" % run)
            else:
                raise RollbackFailed("cannot interpret the state after the interrupted promotion %s: the "
                                     "release differs from the draft receipt but old-%s exists; fix by "
                                     "hand in %s" % (run, run, meta))
        except OSError as exc:
            raise RollbackFailed("recovery of the interrupted promotion %s failed (%s); release tree: %s; "
                                 "inspect %s by hand" % (run, exc.strerror or exc,
                                                         "present" if os.path.lexists(out) else "absent", meta))
        os.unlink(tmp)
    olds = sorted(n for n in os.listdir(meta) if n.startswith("old-") and _real_dir(os.path.join(meta, n)))
    if olds:
        if not os.path.lexists(out):
            raise RollbackFailed("old release copies exist (%s) but there is no release tree - this "
                                 "protocol never leaves that state; inspect %s by hand" % (", ".join(olds), meta))
        rec = read_receipt(cfg)
        if rec is not None and lc.tree_digest(out).value == rec.get("tree_digest"):
            for n in olds:
                shutil.rmtree(os.path.join(meta, n))
                actions.append("C1: removed the leftover %s (the promotion had committed)" % n)
        else:
            raise RollbackFailed("old release copies exist (%s) and the release tree does not match its "
                                 "receipt; inspect %s by hand" % (", ".join(olds), meta))
    for n in sorted(os.listdir(meta)):
        p = os.path.join(meta, n)
        if (n.startswith("staging-") or n.startswith("rollback-")) and _real_dir(p):
            shutil.rmtree(p)
            actions.append("R4: removed the leftover %s" % n)
        elif n.endswith(".part") and os.path.isfile(p) and not os.path.islink(p):
            os.unlink(p)
            actions.append("R4: removed the leftover %s" % n)
    return actions


def ensure_ownership(cfg, lc):
    if not os.path.lexists(cfg.out_dir):
        return None
    rec = read_receipt(cfg)
    if rec is None:
        raise ConfigError("%s exists but has no receipt - it was not made by this tool or the receipt was "
                          "lost; move it away by hand (it is never replaced automatically)" % cfg.out_dir)
    cur = lc.tree_digest(cfg.out_dir).value
    if cur != rec.get("tree_digest"):
        raise ConfigError("%s differs from its receipt (edited by hand?) - move it away or restore it; it is "
                          "never replaced automatically" % cfg.out_dir)
    return rec


def warn_quarantine(cfg):
    q = os.path.join(cfg.meta_dir, "quarantine")
    if os.path.isdir(q):
        items = [n for n in os.listdir(q) if not n.endswith(".DO-NOT-PUBLISH")]
        if items:
            _warn("%d quarantined candidate(s) in %s - they may hold leaked text; review and delete "
                  "them by hand (never publish)" % (len(items), q))


def quarantine(cfg, staging, run):
    q = os.path.join(cfg.meta_dir, "quarantine")
    os.makedirs(q, mode=0o700, exist_ok=True)
    dest = os.path.join(q, run)
    os.rename(staging, dest)
    os.chmod(dest, 0o700)
    with open(os.path.join(q, run + ".DO-NOT-PUBLISH"), "w", encoding="utf-8") as fh:
        fh.write("Failed export candidate %s. It may hold leaked text. Never publish it; delete it "
                 "after review.\n" % run)
    return dest


# ------------------------------------------------------------- staging / gate
def _read_authored(cfg, rel):
    ap = _authored_path(cfg, rel)
    if os.path.realpath(os.path.dirname(ap)) != os.path.normpath(os.path.dirname(ap)):
        raise ConfigError("a folder on the path of %s is a symbolic link (not followed)" % rel)
    return _read_file_nofollow(ap, READ_CAP, "authored file %s" % rel)


def build_staging(cfg, manifest, reader):
    staging = tempfile.mkdtemp(prefix="staging-", dir=cfg.meta_dir)
    try:
        for e in manifest.entries:
            if e.method == "copy-sanitize":
                data = sanitize(e, reader.read(e.source))
            else:
                data = _read_authored(cfg, e.path)
            dest = os.path.join(staging, *e.path.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                _write_all(fd, data)
                os.fchmod(fd, 0o755 if e.executable else 0o644)
            finally:
                os.close(fd)
        for dp, dns, _fns in os.walk(staging):
            for d in dns:
                os.chmod(os.path.join(dp, d), 0o755)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


def _leak_argv(cfg, root, report_path, extra=()):
    argv = [sys.executable, "-I", "-B", LC_PATH, "scan", root, "--policy", cfg.policy_path,
            "--manifest", cfg.manifest_path, "--json-report", report_path]
    argv.extend(extra)
    if cfg.lang == "ko":
        argv.extend(["--lang", "ko"])
    return argv


def _relay(data):
    if data:
        text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
        for line in text.splitlines():
            print("  | " + line)
        sys.stdout.flush()


def run_leak_check(cfg, root, report_path, extra=()):
    argv = _leak_argv(cfg, root, report_path, extra)
    try:
        cp = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=LEAK_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        _relay(exc.output)
        raise ToolError("leak_check did not finish within %d s" % LEAK_TIMEOUT_S)
    except OSError as exc:
        raise ToolError("leak_check could not start: %s" % exc)
    _relay(cp.stdout)
    report = None
    if os.path.isfile(report_path):
        try:
            report = _read_json_file(report_path)
        except (OSError, ValueError):
            report = None
    return cp.returncode, report


def _map_leak(rc):
    return rc if rc in (EXIT_FINDINGS, EXIT_CONTROL_FAILED, EXIT_CONFIG) else EXIT_TOOL


def _leak_summary(rc, report):
    s = {"exit": rc}
    if isinstance(report, dict):
        ctl = report.get("control") or {}
        s.update({"planted": ctl.get("planted"), "caught": ctl.get("caught"), "decoys": ctl.get("decoys"),
                  "decoys_flagged": ctl.get("decoys_flagged"), "findings": report.get("findings_count"),
                  "suppressed": report.get("suppressed") or {}})
    return s


def promote(cfg, staging, receipt):
    """P1 receipt draft, P2 release -> old, P3 staging -> release, P4 receipt
    replace (commit), P5 remove old. Any failure before the commit is rolled back."""
    meta, out, run = cfg.meta_dir, cfg.out_dir, receipt["run_id"]
    tmp, final = _tmp_receipt_path(cfg), _receipt_path(cfg)
    old = os.path.join(meta, "old-" + run)
    rb = os.path.join(meta, "rollback-" + run)
    had_out = os.path.lexists(out)
    step = "P1"
    try:
        _write_json_file(tmp, receipt)
        step = "P2"
        if had_out:
            os.rename(out, old)
        step = "P3"
        os.rename(staging, out)
        step = "P4"
        os.replace(tmp, final)
    except BaseException as exc:
        _rollback(step, had_out, out, old, rb, staging, tmp, exc)
    if had_out:
        try:
            shutil.rmtree(old)
        except OSError as exc:
            _warn("could not remove %s (%s); the next build removes it" % (old, exc.strerror or exc))


def _rollback(step, had_out, out, old, rb, staging, tmp, exc):
    try:
        if step == "P3" and had_out:
            os.rename(old, out)
        elif step == "P4":
            os.rename(out, rb)
            if had_out:
                os.rename(old, out)
            shutil.rmtree(rb, ignore_errors=True)
    except OSError as rexc:
        raise RollbackFailed("promotion failed at %s (%s) and the rollback failed too (%s); release tree: "
                             "%s; previous release copy: %s; fix by hand" % (
                                 step, exc, rexc.strerror or rexc,
                                 "present" if os.path.lexists(out) else "absent",
                                 old if os.path.lexists(old) else "none"))
    for p in (tmp,):
        try:
            if os.path.lexists(p):
                os.unlink(p)
        except OSError:
            pass
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    if isinstance(exc, KeyboardInterrupt):
        raise exc
    raise ToolError("promotion failed at %s (%s); rolled back - the release tree is unchanged" % (step, exc))


def append_run_log(cfg, record):
    try:
        os.makedirs(cfg.meta_dir, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(cfg.meta_dir, "runs.jsonl"),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            _write_all(fd, (json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii"))
        finally:
            os.close(fd)
    except OSError as exc:
        _warn("could not append the run log: %s" % (exc.strerror or exc))


def _versions(lc):
    return {"export": TOOL_VERSION, "leak_check": lc.TOOL_VERSION, "leak_api": lc.API_VERSION,
            "python": sys.version.split()[0], "unicode": unicodedata.unidata_version}


def _classify(exc):
    lc = _LC
    if isinstance(exc, KeyboardInterrupt):
        return EXIT_INTERRUPTED, "INTERRUPTED"
    if isinstance(exc, RollbackFailed):
        return EXIT_ROLLBACK, "ROLLBACK FAILED"
    if isinstance(exc, ConfigError) or (lc is not None and isinstance(exc, lc.ConfigError)):
        return EXIT_CONFIG, "CONFIG ERROR"
    return EXIT_TOOL, "TOOL ERROR"


# -------------------------------------------------------------------- commands
def cmd_check(lc, cfg, policy, manifest, warnings):
    warn_quarantine(cfg)
    for w in warnings:
        _warn(w)
    reader = SourceReader(cfg.source_root, manifest.sources)
    bad_sources = set()
    problems = check_inputs(cfg, manifest, reader, bad_sources)
    for e in manifest.entries:
        if e.method == "copy-sanitize" and e.source not in bad_sources:
            try:
                sanitize(e, reader.read(e.source))
            except ConfigError as exc:
                problems.append(str(exc))
    for rel in unlisted_authored(lc, cfg, manifest)[:20]:
        _warn("authored file not in the manifest (not exported): %s" % rel)
    counts = {m: sum(1 for e in manifest.entries if e.method == m) for m in _METHODS}
    print("CHECK: %d entries (copy-sanitize %d, rewrite %d, new %d); private source files this tool "
          "opens: %d" % (len(manifest.entries), counts["copy-sanitize"], counts["rewrite"], counts["new"],
                         len(manifest.sources)))
    if problems:
        raise ConfigError("inputs are not ready (%d problem(s)):\n  - %s" % (len(problems), "\n  - ".join(problems)))
    print("RESULT: CHECK OK (exit 0) - inputs ready; nothing was written.")
    return EXIT_OK


def _plugin_version(manifest, staging):
    """Version string of the first manifest entry that is a plugin manifest, read
    from the staged copy; None when there is no such entry or no version (E10)."""
    for e in manifest.entries:
        if e.path == ".claude-plugin/plugin.json" or e.path.endswith("/.claude-plugin/plugin.json"):
            try:
                with open(os.path.join(staging, e.path), "r", encoding="utf-8") as fh:
                    v = json.load(fh).get("version")
            except (OSError, ValueError, AttributeError):
                return None
            return v if isinstance(v, str) else None
    return None


def check_version_bump(prev, cand_digest, version, require):
    """Same content -> nothing to say. Changed content with the same plugin version ->
    WARNING, or ConfigError when `require`. Older receipt without the field -> WARNING."""
    if prev is None or version is None or prev.get("tree_digest") == cand_digest:
        return
    if "plugin_version" not in prev:
        _warn("the previous receipt has no plugin_version - cannot compare versions (older receipt)")
        return
    if prev.get("plugin_version") == version:
        msg = ("the release content changed but the plugin version is still %s - bump the version in "
               "plugin.json (and marketplace.json, the plugin README, the hook comment)" % version)
        if require:
            raise ConfigError(msg + " (--require-version-bump)")
        _warn(msg)


def cmd_build(lc, cfg, policy, manifest, warnings, keep_failed, require_bump=False):
    run = _new_run_id()
    record = {"format": RUN_FORMAT, "run_id": run, "cmd": "build", "started": _now(),
              "manifest_sha256": manifest.sha256, "policy_sha256": policy.sha256,
              "counts": {"entries": len(manifest.entries),
                         "copy_sanitize": sum(1 for e in manifest.entries if e.method == "copy-sanitize"),
                         "rewrite": sum(1 for e in manifest.entries if e.method == "rewrite"),
                         "new": sum(1 for e in manifest.entries if e.method == "new")},
              "recovered": [], "quarantine": None, "leak": None, "tree_digest_candidate": None}
    code, verdict = EXIT_TOOL, "TOOL ERROR"
    staging = None
    lock = None
    try:
        lock = acquire_lock(cfg.meta_dir)
        warn_quarantine(cfg)
        for w in warnings:
            _warn(w)
        actions = recover(cfg, lc)
        record["recovered"] = actions
        for a in actions:
            print("RECOVERED: " + a)
        prev = ensure_ownership(cfg, lc)
        reader = SourceReader(cfg.source_root, manifest.sources)
        problems = check_inputs(cfg, manifest, reader)
        if problems:
            raise ConfigError("inputs are not ready (%d problem(s)):\n  - %s" % (len(problems), "\n  - ".join(problems)))
        staging = build_staging(cfg, manifest, reader)
        reports = os.path.join(cfg.meta_dir, "reports")
        os.makedirs(reports, mode=0o700, exist_ok=True)
        report_path = os.path.join(reports, run + ".json")
        record["report"] = os.path.relpath(report_path, cfg.meta_dir)
        rc, report = run_leak_check(cfg, staging, report_path)
        record["leak"] = _leak_summary(rc, report)
        if rc != EXIT_OK:
            code = _map_leak(rc)
            verdict = {EXIT_FINDINGS: "NOT PROMOTED - FINDINGS", EXIT_CONTROL_FAILED: "NOT PROMOTED - CONTROL FAILED",
                       EXIT_CONFIG: "NOT PROMOTED - LEAK_CHECK CONFIG ERROR"}.get(code, "TOOL ERROR")
            if keep_failed:
                record["quarantine"] = quarantine(cfg, staging, run)
                staging = None
                print("QUARANTINED: %s (+ %s.DO-NOT-PUBLISH) - never publish; delete after review"
                      % (record["quarantine"], run))
            dist = lc.tree_digest(cfg.out_dir).value if os.path.lexists(cfg.out_dir) else "absent"
            if code == EXIT_TOOL:
                raise ToolError("leak_check ended abnormally (exit %s)" % rc)
            _emit(code, "RESULT: %s (exit %d) - dist kept at %s." % (verdict, code, dist))
            return code
        if not isinstance(report, dict) or not report.get("tree_digest"):
            raise ToolError("leak_check passed but its report has no tree digest")
        cand = lc.tree_digest(staging)
        if cand.value != report["tree_digest"]:
            raise ToolError("the staging tree changed between the leak check and promotion (digest mismatch)")
        record["tree_digest_candidate"] = cand.value
        plugin_version = _plugin_version(manifest, staging)
        check_version_bump(prev, cand.value, plugin_version, require_bump)
        methods = {e.path: e.method for e in manifest.entries}   # manifest paths are NFC (validated)
        receipt = {"format": RECEIPT_FORMAT, "run_id": run, "created": _now(), "tree_digest": cand.value,
                   "files": [{"path": p, "method": methods.get(p), "mode": m, "bytes": b, "sha256": h}
                             for p, m, h, b in cand.files],
                   "manifest_sha256": manifest.sha256, "policy_sha256": policy.sha256,
                   "config": {"path": cfg.config_path, "source": cfg.config_source, "sha256": cfg.config_sha256},
                   "leak_check": record["leak"], "versions": _versions(lc),
                   "previous_digest": prev.get("tree_digest") if prev else None,
                   "plugin_version": plugin_version}
        if prev is not None and prev.get("tree_digest") == cand.value:
            receipt["previous_digest"] = prev.get("previous_digest")
            _write_json_atomic(_receipt_path(cfg), receipt)
            code, verdict = EXIT_OK, "UNCHANGED"
            _emit(code, "RESULT: UNCHANGED (exit 0) - dist=%s (same as the receipt)." % cand.value)
        else:
            promote(cfg, staging, receipt)
            staging = None
            code, verdict = EXIT_OK, "PROMOTED"
            _emit(code, "RESULT: PROMOTED (exit 0) - dist=%s. Next: semantic review of exactly this digest."
                  % cand.value)
        return code
    except BaseException as exc:
        code, verdict = _classify(exc)
        record["error"] = str(exc)[:500]
        raise
    finally:
        if staging and os.path.isdir(staging):
            shutil.rmtree(staging, ignore_errors=True)
        record["exit"], record["verdict"], record["ended"] = code, verdict, _now()
        try:
            record["dist_digest_after"] = (lc.tree_digest(cfg.out_dir).value
                                           if os.path.lexists(cfg.out_dir) else None)
        except Exception:
            record["dist_digest_after"] = "unavailable"
        append_run_log(cfg, record)
        if lock is not None:
            os.close(lock)


def cmd_verify(lc, cfg, policy, manifest, expect, clone, git_metadata):
    if expect and not _EXPECT_RX.match(expect):
        raise ConfigError("--expect must look like sha256:<64 lowercase hex digits>")
    if git_metadata and not clone:
        raise ConfigError("--git-metadata applies to --clone (the release tree itself has no git metadata)")
    if not os.path.isdir(cfg.meta_dir) or not os.path.isfile(_receipt_path(cfg)):
        raise ConfigError("no receipt in %s - run build first" % cfg.meta_dir)
    run = _new_run_id()
    record = {"format": RUN_FORMAT, "run_id": run, "cmd": "verify", "started": _now(),
              "expect": expect, "clone": bool(clone), "git_metadata": git_metadata}
    code, verdict = EXIT_TOOL, "TOOL ERROR"
    lock = acquire_lock(cfg.meta_dir, shared=True, create=False)
    try:
        leftovers = sorted(n for n in os.listdir(cfg.meta_dir)
                           if n.startswith(("staging-", "old-", "rollback-")) or n == "receipt.json.tmp"
                           or n.endswith(".part"))
        if leftovers:
            raise ConfigError("an earlier build did not finish (%s) - run build to recover first; an "
                              "uncommitted tree is never verified" % ", ".join(leftovers))
        if not _real_dir(cfg.out_dir):
            raise ConfigError("no release tree at %s" % cfg.out_dir)
        rec = read_receipt(cfg)
        cur = lc.tree_digest(cfg.out_dir).value
        print("RECEIPT: run %s, created %s, digest %s" % (rec.get("run_id"), rec.get("created"), rec["tree_digest"]))
        if rec.get("policy_sha256") != policy.sha256:
            print("NOTE: the policy changed since that build - this verify uses the CURRENT policy")
        if cur != rec["tree_digest"]:
            code, verdict = EXIT_FINDINGS, "NOT VERIFIED - DIGEST MISMATCH"
            _emit(code, "RESULT: %s (exit 10) - dist is %s, receipt says %s; this is not the reviewed tree."
                  % (verdict, cur, rec["tree_digest"]))
            return code
        if expect and expect != rec["tree_digest"]:
            code, verdict = EXIT_FINDINGS, "NOT VERIFIED - EXPECT MISMATCH"
            _emit(code, "RESULT: %s (exit 10) - receipt digest %s differs from the expected %s."
                  % (verdict, rec["tree_digest"], expect))
            return code
        reports = os.path.join(cfg.meta_dir, "reports")
        os.makedirs(reports, mode=0o700, exist_ok=True)
        rc, _rep = run_leak_check(cfg, cfg.out_dir, os.path.join(reports, run + "-verify.json"))
        if rc != EXIT_OK:
            code = _map_leak(rc)
            if code == EXIT_TOOL:
                raise ToolError("leak_check ended abnormally (exit %s)" % rc)
            verdict = "NOT VERIFIED - LEAK CHECK"
            _emit(code, "RESULT: %s (exit %d) - the current policy rejects the release tree." % (verdict, code))
            return code
        if clone:
            cpath = os.path.abspath(clone)
            try:
                st = os.lstat(cpath)
            except OSError:
                raise ConfigError("--clone path does not exist: %s" % cpath)
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise ConfigError("--clone must be a real folder: %s" % cpath)
            extra = ["--allow-git-dir"] + (["--git-metadata"] if git_metadata else [])
            rc2, _rep2 = run_leak_check(cfg, cpath, os.path.join(reports, run + "-clone.json"), extra)
            if rc2 != EXIT_OK:
                code = _map_leak(rc2)
                if code == EXIT_TOOL:
                    raise ToolError("leak_check on the clone ended abnormally (exit %s)" % rc2)
                verdict = "NOT VERIFIED - CLONE LEAK CHECK"
                _emit(code, "RESULT: %s (exit %d) - the clone has findings." % (verdict, code))
                return code
            dc = lc.tree_digest(cpath, allow_git_dir=True).value
            if dc != rec["tree_digest"]:
                code, verdict = EXIT_FINDINGS, "NOT VERIFIED - CLONE DIFFERS"
                _emit(code, "RESULT: %s (exit 10) - clone digest %s differs from the receipt %s."
                      % (verdict, dc, rec["tree_digest"]))
                return code
        code, verdict = EXIT_OK, "VERIFIED"
        _emit(code, "RESULT: VERIFIED (exit 0) - dist == receipt %s%s; the current policy finds nothing%s."
              % (rec["tree_digest"], " == expected" if expect else "",
                 "; clone matches%s" % (" incl. git metadata" if git_metadata else "") if clone else ""))
        return code
    except BaseException as exc:
        code, verdict = _classify(exc)
        record["error"] = str(exc)[:500]
        raise
    finally:
        record["exit"], record["verdict"], record["ended"] = code, verdict, _now()
        append_run_log(cfg, record)
        os.close(lock)


# ------------------------------------------------------------------ examples
def example_manifest():
    return {
        "format": MANIFEST_FORMAT,
        "substitutions": {"S01": {"find": "owner@private.example", "replace": "maintainers@example.invalid"}},
        "entries": [
            {"path": "README.md", "method": "new"},
            {"path": "docs/guide.md", "method": "rewrite", "basis": "notes/guide-draft.md (record only)"},
            {"path": "plugin/hooks.json", "method": "copy-sanitize", "source": "plug/hooks.json", "apply": []},
            {"path": "tools/scan.py", "method": "copy-sanitize", "source": "tool/scan.py",
             "apply": [{"sub": "S01", "count": 1},
                       {"find": "DEFAULT_DIR = \"/srv/private\"", "replace": "DEFAULT_DIR = config_dir()",
                        "count": 1}],
             "executable": True},
        ],
    }


def example_config():
    return {
        "charter": "docs/CHARTER.md", "gate_log": "logs/GATE-LOG.md", "claim_log": "logs/CLAIM-LOG.md",
        "handoff_dir": "handoffs/", "rules_file": "~/.claude/CLAUDE.md",
        "transcripts_dir": "~/.claude/projects/<project>", "lang": "en",
        "export": {"source_root": "~/path/to/private-notes", "authored_root": "~/path/to/public-draft",
                   "out_dir": "~/path/to/dist", "manifest": "~/.config/collab-os/export/manifest.json",
                   "policy": "~/.config/collab-os/export/policy.json"},
    }


def cmd_print_example(kind):
    lc = load_leak_check()
    if kind == "manifest":
        obj = example_manifest()
    elif kind == "policy":
        obj = lc.example_policy()
    elif kind == "config":
        obj = example_config()
    else:
        raise ConfigError("--print-example needs one of: manifest, policy, config")
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return EXIT_OK


# -------------------------------------------------------------------- selftest
_AUDIT = {"on": False, "events": []}


def _audit_hook(event, args):
    if not _AUDIT["on"]:
        return
    if event in ("open", "os.listdir", "os.scandir", "glob.glob", "os.rename", "os.remove",
                 "os.mkdir", "os.rmdir", "shutil.rmtree", "shutil.copyfile"):
        path = args[0] if args else None
        if isinstance(path, (str, bytes, os.PathLike)):
            path = os.fsdecode(path)
            flags = args[2] if event == "open" and len(args) > 2 else None
            mode = args[1] if event == "open" and len(args) > 1 else None
            _AUDIT["events"].append((event, path, mode, flags))


def _slurp(path, text=False):
    with open(path, "r" if text else "rb", **({"encoding": "utf-8"} if text else {})) as fh:
        return fh.read()


def _write(path, data, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.chmod(path, mode)


def cmd_selftest():
    lc = load_leak_check()
    sys.addaudithook(_audit_hook)
    results = []
    tmp = tempfile.mkdtemp(prefix="export-selftest-")
    try:
        src = os.path.join(tmp, "source")
        pub = os.path.join(tmp, "public")
        cfgd = os.path.join(tmp, "cfg")
        work = os.path.join(tmp, "work")
        os.makedirs(work)
        _write(os.path.join(src, "plug", "hooks.json"), '{"hooks": {"SessionStart": []}}\n')
        _write(os.path.join(src, "tool", "scan.py"), "owner = 'owner@private.example'\n"
               "DEFAULT_DIR = \"/srv/private\"\nprint(DEFAULT_DIR)\n")
        _write(os.path.join(src, "SECRET-unlisted.txt"), "never opened\n")
        _write(os.path.join(src, "DRAFT-notes.md"), "denied\n")
        _write(os.path.join(pub, "README.md"), "Readme for the demo.\n")
        _write(os.path.join(pub, "docs", "guide.md"), "A guide.\n")
        manifest = example_manifest()
        policy = lc.example_policy()
        _write(os.path.join(cfgd, "policy.json"), json.dumps(policy, ensure_ascii=False))
        mpath = os.path.join(cfgd, "manifest.json")
        _write(mpath, json.dumps(manifest))
        conf = example_config()
        conf["export"] = {"source_root": src, "authored_root": pub, "out_dir": os.path.join(work, "dist"),
                          "manifest": mpath, "policy": os.path.join(cfgd, "policy.json")}
        cpath = os.path.join(cfgd, "config.json")
        _write(cpath, json.dumps(conf))
        dist = os.path.join(work, "dist")
        meta = os.path.join(work, "dist-meta")

        def run(*argv):
            print("--- selftest: export.py %s" % " ".join(argv))
            return run_cli(list(argv))

        def leftovers():
            if not os.path.isdir(meta):
                return []
            return [n for n in os.listdir(meta) if n.startswith(("staging-", "old-", "rollback-"))
                    or n == "receipt.json.tmp"]

        _AUDIT["events"].clear()
        _AUDIT["on"] = True
        c1 = run("build", "--config", cpath)
        _AUDIT["on"] = False
        d1 = lc.tree_digest(dist).value if os.path.isdir(dist) else None
        scan_py = _slurp(os.path.join(dist, "tools", "scan.py"), text=True) if d1 else ""
        same_hooks = d1 is not None and _slurp(os.path.join(dist, "plugin", "hooks.json")) == \
            _slurp(os.path.join(src, "plug", "hooks.json"))
        results.append(("S1 build", c1 == 0 and d1 is not None and "config_dir()" in scan_py
                        and "private.example" not in scan_py and same_hooks
                        and os.path.isfile(os.path.join(meta, "receipt.json"))))
        src_events = [ev for ev in _AUDIT["events"] if lc._realpath_inside(ev[1], src)]
        src_r = os.path.realpath(src)
        opened = sorted(os.path.relpath(os.path.realpath(ev[1]), src_r) for ev in src_events if ev[0] == "open")
        listing = [ev for ev in src_events if ev[0] in ("os.listdir", "os.scandir", "glob.glob")]
        writes = [ev for ev in src_events if ev[0] != "open" and ev[0] not in ("os.listdir", "os.scandir", "glob.glob")
                  or (ev[0] == "open" and isinstance(ev[3], int)
                      and ev[3] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
                  or (ev[0] == "open" and isinstance(ev[2], str) and any(c in ev[2] for c in "wax+"))]
        results.append(("S7 source access", opened == ["plug/hooks.json", "tool/scan.py"] and not listing
                        and not writes))
        ino = os.stat(dist).st_ino if d1 else None
        c2 = run("build", "--config", cpath)
        results.append(("S2 unchanged", c2 == 0 and lc.tree_digest(dist).value == d1 and os.stat(dist).st_ino == ino))
        readme = os.path.join(pub, "README.md")
        original = _slurp(readme)
        _write(readme, original + ("mentions " + "zq" + "canary" + " here\n").encode("utf-8"))
        c3 = run("build", "--config", cpath)
        _write(readme, original)
        results.append(("S3 leak keeps dist", c3 == 10 and lc.tree_digest(dist).value == d1 and not leftovers()))
        bad = json.loads(json.dumps(manifest))
        bad["entries"].append({"path": "notes.md", "method": "copy-sanitize", "source": "DRAFT-notes.md", "apply": []})
        _write(mpath, json.dumps(bad))
        _AUDIT["events"].clear()
        _AUDIT["on"] = True
        c4 = run("check", "--config", cpath)
        _AUDIT["on"] = False
        touched = [ev for ev in _AUDIT["events"] if ev[1].endswith("DRAFT-notes.md")]
        results.append(("S4 deny pattern", c4 == 30 and not touched))
        bad = json.loads(json.dumps(manifest))
        bad["entries"][3]["apply"][1]["count"] = 2
        _write(mpath, json.dumps(bad))
        results.append(("S5 rule count", run("check", "--config", cpath) == 30))
        _write(mpath, json.dumps(manifest))
        guide = os.path.join(pub, "docs", "guide.md")
        g = _slurp(guide)
        os.unlink(guide)
        results.append(("S6 missing file", run("check", "--config", cpath) == 30))
        _write(guide, g)
        c8a = run("verify", "--config", cpath, "--expect", d1 or "sha256:" + "0" * 64)
        target = os.path.join(dist, "README.md")
        keep = _slurp(target)
        os.chmod(target, 0o644)
        with open(target, "ab") as fh:
            fh.write(b"x")
        c8b = run("verify", "--config", cpath)
        with open(target, "wb") as fh:
            fh.write(keep)
        results.append(("S8 verify + tamper", c8a == 0 and c8b == 10))
    finally:
        _AUDIT["on"] = False
        shutil.rmtree(tmp, ignore_errors=True)
    print("--- selftest summary")
    ok = True
    for name, passed in results:
        print("  %-22s %s" % (name, "pass" if passed else "FAIL"))
        ok = ok and passed
    if ok and len(results) == 8:
        print("RESULT: SELFTEST PASS (exit 0) - 8/8 scenarios.")
        return EXIT_OK
    print("RESULT: SELFTEST FAILED (exit 20) - do not trust this tool until fixed.")
    return EXIT_CONTROL_FAILED


# ------------------------------------------------------------------------- cli
class _ArgParser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigError("arguments: " + message)


def build_parser():
    p = _ArgParser(prog="export.py", description="Allow-list export with a fail-closed leak gate.")
    sub = p.add_subparsers(dest="cmd")
    for name in ("check", "build", "verify"):
        s = sub.add_parser(name)
        s.add_argument("--config")
        s.add_argument("--debug", action="store_true")
        if name == "build":
            s.add_argument("--keep-failed", action="store_true")
            s.add_argument("--require-version-bump", action="store_true")
        if name == "verify":
            s.add_argument("--expect")
            s.add_argument("--clone")
            s.add_argument("--git-metadata", action="store_true")
    return p


_KO = {
    EXIT_OK: "판정: 통과 - dist 가 바뀌었거나 "
             "그대로다. 의미 검토는 별도.",
    EXIT_FINDINGS: "판정: 적발 - dist 는 그대로다.",
    EXIT_CONTROL_FAILED: "판정: 대조군 실패 - dist 는 그대로다.",
    EXIT_CONFIG: "판정: 설정 오류 - 아무것도 복사하지 "
                 "않았다.",
    EXIT_TOOL: "판정: 도구 오류 - dist 는 그대로다.",
    EXIT_ROLLBACK: "판정: 되돌리기 실패 - 사람이 "
                   "상태를 고쳐야 한다.",
}


def _emit(code, line):
    if _STATE["lang"] == "ko" and code in _KO:
        print(_KO[code])
    print(line)
    sys.stdout.flush()


def main(argv):
    if argv[:1] == ["--version"]:
        lc = load_leak_check()
        print("export %s / leak_check %s (api %d)" % (TOOL_VERSION, lc.TOOL_VERSION, lc.API_VERSION))
        return EXIT_OK
    if argv[:1] == ["--selftest"]:
        return cmd_selftest()
    if argv[:1] == ["--print-example"]:
        return cmd_print_example(argv[1] if len(argv) > 1 else "")
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        raise ConfigError("missing command (check, build, verify, --selftest, --print-example, --version)")
    if os.name != "posix":
        raise ConfigError("only macOS and Linux are supported")
    _STATE["debug"] = args.debug
    lc = load_leak_check()
    cfg = load_export_config(lc, args.config)
    _STATE["lang"] = cfg.lang
    policy = lc.load_policy(cfg.policy_path)
    manifest, warnings = load_manifest(lc, cfg.manifest_path, policy)
    if args.cmd == "check":
        return cmd_check(lc, cfg, policy, manifest, warnings)
    if args.cmd == "build":
        return cmd_build(lc, cfg, policy, manifest, warnings, args.keep_failed, args.require_version_bump)
    return cmd_verify(lc, cfg, policy, manifest, args.expect, args.clone, args.git_metadata)


def run_cli(argv):
    """main() with the exit-code mapping; used by the entry point and selftest."""
    try:
        return main(argv)
    except SystemExit:
        raise
    except BaseException as exc:
        code, name = _classify(exc)
        if _STATE["debug"] and code in (EXIT_TOOL, EXIT_ROLLBACK):
            traceback.print_exc()
        if code == EXIT_INTERRUPTED:
            print("RESULT: INTERRUPTED (exit 130) - NOT committed; the next build rolls back any half-done promotion.")
            return code
        reason = str(exc)
        known_tool = isinstance(exc, ToolError) or (_LC is not None and isinstance(exc, _LC.ToolError))
        if code == EXIT_TOOL and not known_tool:
            reason = "%s: %s" % (type(exc).__name__, exc)
        tail = {EXIT_CONFIG: "nothing copied", EXIT_TOOL: "dist kept",
                EXIT_ROLLBACK: "fix the printed state by hand before the next run"}[code]
        _emit(code, "RESULT: %s (exit %d) - %s; %s." % (name, code, reason, tail))
        return code


def _entry():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
    if sys.version_info < (3, 9):
        print("RESULT: CONFIG ERROR (exit 30) - Python 3.9 or newer is required; nothing copied.")
        sys.exit(EXIT_CONFIG)
    _STATE["debug"] = "--debug" in sys.argv[1:]
    sys.exit(run_cli(sys.argv[1:]))


if __name__ == "__main__":
    _entry()
