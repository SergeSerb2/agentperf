"""Remove private fields from a result so it can be shared in public.

What is removed or generalized (see SECURITY.md):

- hostname, label (replaced with a generic profile name), run_id
- workdir, filesystem device and mount paths, home-directory paths anywhere
- top process list and physical disk friendly names
- security product brand names (kept only as "detected / active" flags)
- absolute tool paths (kept as file names only)

What stays: every measurement, plus generic system facts that explain the
measurements (CPU model, OS version, filesystem type, core counts, RAM,
power and indexing flags). A shared result must still explain itself.
Fields agentperf does not write are dropped, never scrubbed and kept.
"""

from __future__ import annotations

import copy
import ipaddress
import os
import re
import unicodedata
from typing import Dict, List, Tuple

# Keys dropped from system outright.
_DROP_SYSTEM = ("hostname", "workdir", "cwd", "top_processes", "physical_disks",
                "fs_device", "fs_mount", "model_id", "user", "home")

# Server names in UNC paths: host names, not user names.
_SERVER_CAPTURE = (
    re.compile(r"\\\\([^\\\s\"'/]+)"),          # UNC server name
    re.compile(r"(?<![\w:])//([^\s\"'/]+)/"),    # //server/share
)
_HOME_CAPTURE = [
    # POSIX user folders can contain spaces and apostrophes too
    # ("/Users/Alice Smith", "/Users/O'Brien"). Matching is
    # case-insensitive: "/USERS/..." and "c:/users/..." are the same
    # paths on the filesystems that produce them.
    # The name ends at a separator, a double quote, a line end, or the
    # end of the string; separators may repeat or mix ("C:\Users/Bob").
    # The folder after a "home", "Users", or "Documents and Settings"
    # segment is a user name in every path form: "/home/bob",
    # "C:\Users\bob", "/mnt/c/Users/bob", "\\wsl.localhost\Ubuntu\home\bob",
    # "\\server\Users\al". A Windows user folder can itself contain
    # spaces ("Alice Smith").
    re.compile(r"(?:^|[\\/])(?:home|Users|Documents and Settings)[\\/]+"
               r"([^\\/\"\n]+?)(?=[\\/\"\n]|$)", re.IGNORECASE),
    # A folder name that itself starts with a quote ('"bob').
    re.compile(r"(?:^|[\\/])(?:home|Users|Documents and Settings)[\\/]+"
               r"(\"[^\\/\n]+?)(?=[\\/\n]|$)", re.IGNORECASE),
    *_SERVER_CAPTURE,
    # ~user/... and a bare ~user, with any user name ("~_bob",
    # "~123bob", "~.bob", accented letters) that has a letter in it.
    re.compile(r"~([^\s/\\\"~<][^\s/\\\"~]*)[/\\]"),
    re.compile(r"(?<![\w~])~([^\s/\\\"~<]*[^\W\d_][^\s/\\\"~]*)"),
]
# Home paths: the subpath under a home directory is private too (client
# names, project names), so the whole path token is generalized. Volume
# names under /Volumes can name clients or projects as well. Path
# segments may contain spaces ("/Volumes/Client Merger Documents",
# r"C:\Users\Alice Smith\..."), so these patterns must not stop at a
# space inside a path. They run to the next quote or line end instead,
# which can over-scrub words that follow a path in free text; for a
# privacy filter that is the safe direction.
# Path tokens run to the next double quote or line end. A single quote
# does not end a token: "/Users/O'Brien/..." is one path, and stopping
# at the apostrophe leaked the rest of the surname. Over-scrubbing
# words that follow a quoted path is the safe direction (see the
# residual check, which withholds anything still identifying). Folder
# names match case-insensitively, as the filesystems treat them.
# A dotted domain's last label holds a letter, as every real top-level
# domain does, so "python@3.12" (a Homebrew path) is not an address; a
# single-label host may be anything ("alice@buildbox", "alice@123").
_EMAIL = re.compile(r'(?:"[^"\n]*"|[\w.+-]+)@(?:(?:[\w-]+\.)+[\w-]*[^\W\d_][\w-]*'
                    r'|[\w-]+(?![\w.-]))')
_IPV4 = re.compile(r"(?<![0-9])(?<!\d\.)(?:\d{1,3}\.){3}\d{1,3}(?![0-9]|\.\d)")
_MAC = re.compile(r"(?<![0-9A-Za-z])(?:(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}"
                  r"|[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})(?![0-9A-Za-z])")
_TSNET = re.compile(r"[\w.-]*\.ts\.net(?![0-9A-Za-z-])", re.IGNORECASE)
_HOME_RE = [
    # UNC paths name a server, a share, and a private folder tree,
    # in backslash or forward-slash form.
    (re.compile(r"\\\\[^\n\"]+"), r"\\<host>\<share>"),
    (re.compile(r"(?<![\w:])//[^\s\"'/]+/[^\n\"]*"), "//<host>/<share>"),
    # Tilde paths are home paths: "~alice/Secret", "~/Client Plans".
    # Any user name, and a hand-typed "~<user>..." look-alike too.
    (re.compile(r"~[^\s/\\\"~][^\s/\\\"~]*[/\\][^\n\"]*"), "~<user>"),
    (re.compile(r"~[/\\][^\n\"]*"), "~<user>"),
    (re.compile(r"~<user>[^\s/\\\"]+"), "~<user>"),
    (re.compile(r"(?<![\w~])~[^\s/\\\"~<]*[^\W\d_][^\s/\\\"~]*"), "~<user>"),
    # The user folder itself may contain spaces, so the whole token to
    # the next quote or line end is generalized, as with Windows below.
    (re.compile(r"/Users/[^\n\"]+", re.IGNORECASE), "/Users/<user>"),
    (re.compile(r"/home/[^\n\"]+", re.IGNORECASE), "/home/<user>"),
    (re.compile(r"/root(?:/[^\n\"]*)?", re.IGNORECASE), "/root/<user>"),
    (re.compile(r"/Volumes/[^\n\":]+:", re.IGNORECASE), "/Volumes/<volume>:"),
    (re.compile(r"/Volumes/[^\n\"]+", re.IGNORECASE), "/Volumes/<volume>"),
    (re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\n\"]+", re.IGNORECASE),
     r"C:\Users\<user>"),
    (re.compile(r"[A-Za-z]:[\\/]Documents and Settings[\\/][^\n\"]+",
                re.IGNORECASE), r"C:\Users\<user>"),
    # Addresses end at anything that is not a letter or digit; an
    # underscore ("_10.0.0.1_") does not shelter them.
    (_EMAIL, "<email>"),
    (_IPV4, "<ip>"),
    (_MAC, "<mac>"),
    (_TSNET, "<host>"),
]
# Runs of hex digits, colons, and dots that could hold an IPv6 address.
# A label in front ("ip:fe80::1") or a stray colon must not hide one, so
# each run is searched for its longest valid address, cut at colons,
# and the *whole* address (prefix included) is replaced, never a suffix.
_IPV6_RUN = re.compile(r"[0-9A-Fa-f:.]*:[0-9A-Fa-f:.]*(?:%[\w.-]+)?")


def _ipv6_spans(value):
    """(start, end) of each IPv6 address in *value*, longest first per run.

    Every cut at a colon is tried, so a label in front ("IPv6:2001:db8::4",
    whose "6" is a hex digit) cannot hide the address behind it. An
    address must not touch a letter or digit on either side, which keeps
    "std::string" and similar text intact."""
    spans = []
    for match in _IPV6_RUN.finditer(value):
        run, base = match.group(0), match.start()
        pos = 0
        while pos < len(run):
            best = None
            starts = [pos] + [i + 1 for i in range(pos, len(run)) if run[i] == ":"]
            for i in starts:
                left = value[base + i - 1] if base + i else ""
                if left.isalnum():
                    continue
                for j in range(len(run), i + 1, -1):
                    token = run[i:j]
                    if token.count(":") < 2 or token.endswith("."):
                        continue
                    if best and len(token) <= best[1] - best[0]:
                        break
                    right = value[base + j] if base + j < len(value) else ""
                    if right.isalnum():
                        continue
                    try:
                        addr = ipaddress.ip_address(token.split("%")[0])
                    except ValueError:
                        continue
                    if isinstance(addr, ipaddress.IPv6Address) \
                            and not (addr.is_unspecified or addr.is_loopback):
                        best = (i, j)
                        break
            if not best:
                break
            spans.append((base + best[0], base + best[1]))
            pos = best[1]
    return spans


def _scrub_ipv6(value: str, counter=None) -> str:
    out, pos = [], 0
    for a, b in _ipv6_spans(value):
        out.append(value[pos:a] + "<ip>")
        pos = b
        if counter is not None:
            counter[0] += 1
    return "".join(out) + value[pos:]


# Network suffixes of a host name ("mac.local"), not part of a name.
_DNS_SUFFIXES = {"local", "localdomain", "lan"}
# Generic folders, not people (documented in SECURITY.md).
_NOT_NAMES = {"<user>", "shared", "public", "default", "users", "home"}


def _candidate_names(doc) -> List[str]:
    """User/host names worth scrubbing as bare words: home-path users + hostname."""
    names: List[str] = []

    def keep(name, minimum=2):
        if len(name) >= minimum and name.lower() not in _NOT_NAMES \
                and name not in names:
            names.append(name)

    def add(name, user=False):
        name = unicodedata.normalize("NFC", (name or "").strip())
        if not name:
            return
        # Two-letter names ("Al", a host called "pc") are scrubbed as
        # whole words; one letter is too generic to be a name.
        keep(name)
        folded = name.casefold()
        if folded != name.lower():
            add(folded, user)  # "Groß" is also "GROSS"
        # "_bob.DOMAIN" (a qualified Windows profile folder), ".bob", and
        # "bob-" all hold the plain account name "bob".
        account = name.split(".")[0] or name
        core = re.sub(r"^[\W_]+|[\W_]+$", "", account)
        keep(account)
        keep(core)
        keep(re.sub(r"^[\W_]+|[\W_]+$", "", name))
        first = re.split(r"[\W_]+", core, maxsplit=1)[0]
        keep(first)
        # A captured "Alice Smith" also makes bare "Smith" identifying.
        for part in name.split():
            keep(part, 3)
        # So does the fragment of a hyphenated host or folder name
        # ("alicez" in "alicez-mac.local"); short fragments are too
        # generic to scrub as bare words.
        for part in re.split(r"[^\w]+|_", name):
            if part.lower() not in _DNS_SUFFIXES:
                keep(part, 3 if user else 4)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(key)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            for rx in _HOME_CAPTURE:
                for match in rx.finditer(node):
                    add(match.group(1), user=rx not in _SERVER_CAPTURE)

    def hosts(node):
        # system.hostname wherever a system object sits, nested too.
        if isinstance(node, dict):
            value = node.get("hostname")
            if isinstance(value, str):
                add(value)
                add(value.split(".")[0])
            for child in node.values():
                hosts(child)
        elif isinstance(node, list):
            for child in node:
                hosts(child)

    def literal_homes(node):
        # The workdir (and the other dropped path fields) is a path, not
        # free text: its home folder is the whole segment after "home" or
        # "Users", quotes and spaces included ('a"bob').
        system = node.get("system") if isinstance(node, dict) else None
        if isinstance(system, dict):
            user = system.get("user")
            if isinstance(user, str):
                add(user, user=True)
            for key in ("workdir", "cwd", "home", "fs_mount", "fs_device"):
                value = system.get(key)
                if isinstance(value, str):
                    parts = [p for p in re.split(r"[\\/]+", value) if p]
                    for i, part in enumerate(parts[:-1]):
                        if part.lower() in ("home", "users", "documents and settings"):
                            add(parts[i + 1], user=True)
                    if key == "home" and parts:
                        add(parts[-1], user=True)  # the home folder itself

    walk(doc)
    hosts(doc)
    literal_homes(doc)
    return names


def _scrub_paths(value, counter=None):
    """Paths and addresses only: no bare-name rewriting."""
    if not isinstance(value, str):
        return value
    value = _scrub_ipv6(value, counter)
    for rx, repl in _HOME_RE:
        value, n = rx.subn(lambda m, r=repl: r, value)
        if counter is not None:
            counter[0] += n
    return value


def _scrub_text(value, names=(), counter=None):
    """Paths, addresses, and bare user/host names: free text only."""
    if not isinstance(value, str):
        return value
    # One form for every text: a decomposed "Bo\u0308b" from a macOS path
    # and a composed "B\u00f6b" elsewhere are the same name.
    value = unicodedata.normalize("NFC", value)
    value = _scrub_paths(value, counter)
    for name in names:
        # Case variants of a known name are the same name ("ALICE").
        # Only a letter or digit joins a name to its neighbours, so
        # "_Bob_" and "bob-" are found as well as "Bob".
        value, n = re.subn(r"(?<![^\W_])" + re.escape(name) + r"(?![^\W_])",
                           "<name>", value, flags=re.IGNORECASE)
        if counter is not None:
            counter[0] += n
    return value


# What must never survive a scrub, placeholder forms excepted. A
# scrubbable string still matching one of these after scrubbing is
# withheld whole: fields the scrubber cannot clean are dropped, never
# passed through. Matching is case-insensitive, like _HOME_RE: a
# residual "C:\users\..." is the same path as "C:\Users\...". The
# residual check runs only on strings in scrubbable positions (free
# text, paths, and keys, path forms only for keys): structural schema
# strings are preserved by the schema-aware walk in anonymize() and
# never withheld, however a user or host name collides with them.
_RESIDUAL_RE = [
    re.compile(r"\\\\(?!<host>)", re.IGNORECASE),
    re.compile(r"(?<![\w:])//(?!<host>)[^\s\"'/]+/", re.IGNORECASE),
    re.compile(r"~(?!<user>)[^\s/\\\"~][^\s/\\\"~]*[/\\]"),
    re.compile(r"~[/\\]"),
    re.compile(r"(?<![\w~])~(?!<user>)[^\W\d]"),
    re.compile(r"/Users/(?!<user>)", re.IGNORECASE),
    re.compile(r"/home/(?!<user>)", re.IGNORECASE),
    re.compile(r"/root/(?!<user>)", re.IGNORECASE),
    re.compile(r"/Volumes/(?!<volume>)", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/]Users[\\/](?!<user>)", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/]+Documents and Settings[\\/]", re.IGNORECASE),
    re.compile(r"(?<![\w~])~(?!<user>)[^\s/\\\"~<]*[^\W\d_]"),
    _MAC, _EMAIL, _IPV4, _TSNET,
]


# A home-path placeholder followed by a quote and more path on the same
# line: the folder name itself may have held a quote ('Bob"Smith', 'Bob"
# Smith'), so the scrub stopped early. Fail closed.
_PLACEHOLDER_SPLIT = re.compile(r"<(?:user|share|volume|path)>\"[^\n]*[/\\]")


# Any absolute path left in free text after the home-path scrub
# ("D:\Client Plans\Acme\agentperf", "/opt/build.10.0.0.5/bin/bash"): its
# folders can name a client, a project, or a machine, so the path is
# replaced whole. A quoted path ends at its closing quote; an unquoted one
# runs to the next double quote or line end, like home paths, because a
# folder name can hold spaces. A trailing tool name ("bash", "git.exe")
# stays, so a skip reason still says what was missing. URLs ("https://")
# and units ("MB/s") are not paths.
_PATH_START = re.compile(r"(?<![\w<>.:~/\\-])(?:[A-Za-z]:[\\/]|\\\\|/)(?=[^\s\"'])")
_KEPT_PATH = re.compile(r"(?:/Users/<user>|/home/<user>|/root/<user>|/Volumes/<volume>"
                        r"|[A-Za-z]:\\Users\\<user>|\\\\<host>\\<share>|//<host>/<share>)"
                        r"[:,;.)\]]*\Z")
_KEPT_BASENAMES = frozenset(("bash", "sh", "zsh", "dash", "fish", "ksh", "tcsh",
                             "csh", "cmd", "powershell", "pwsh", "git", "rg",
                             "grep", "tar", "bsdtar", "node", "npx", "python",
                             "python3", "claude", "codex", "true", "rg.exe"))


_DIAGNOSTIC = re.compile(
    r"(?:exited|failed|returned|timed|not|is|was|were|has|had|does|did|"
    r"cannot|could|can't|denied|missing|with|code|exit|error|\(|\[|\d)",
    re.IGNORECASE)


def _path_end(text, start):
    line_end = text.find("\n", start)
    line_end = len(text) if line_end == -1 else line_end
    quote = text[start - 1] if start and text[start - 1] in "'\"" else None
    if quote:
        # The closing quote is one not followed by a letter or digit
        # ("O'Brien" keeps going).
        pos = start
        while True:
            close = text.find(quote, pos, line_end)
            if close == -1:
                return line_end
            if close + 1 >= len(text) or not text[close + 1].isalnum():
                return close
            pos = close + 1
    # Unquoted: a folder name can hold spaces, so the path runs to the
    # line end, unless a plain diagnostic follows and nothing after it
    # looks like a path ("…/bash exited with code 7").
    for gap in re.finditer(r"\s+", text[start:line_end]):
        rest = text[start + gap.end():line_end]
        if _DIAGNOSTIC.match(rest) and not re.search(r"[\\/]", rest):
            return start + gap.start()
    return line_end


def _generalize_paths(text):
    out, pos = [], 0
    for match in _PATH_START.finditer(text):
        start = match.start()
        if start < pos:
            continue
        end = _path_end(text, start)
        token = text[start:end]
        if _KEPT_PATH.match(token):
            continue
        tail = re.sub(r"[:,;.)\]]+\Z", "", token)
        base = re.split(r"[\\/]", tail.rstrip("\\/"))[-1]
        keep = re.sub(r"\.exe\Z", "", base.lower()) in _KEPT_BASENAMES
        out.append(text[pos:start] + ("<path>/" + base if keep else "<path>")
                   + token[len(tail):])
        pos = end
    return "".join(out) + text[pos:]


def _camel_joined(text, name):
    """A short name glued to a word at a case or digit change ("AlLaptop",
    "pcLaptop", "LaptopAl", "pc2"), which whole-word matching misses."""
    for match in re.finditer(re.escape(name), text, re.IGNORECASE):
        i, j = match.start(), match.end()
        left = i == 0 or not text[i - 1].isalnum() \
            or (text[i - 1].islower() and text[i].isupper()) \
            or text[i - 1].isdigit() != text[i].isdigit()
        right = j == len(text) or not text[j].isalnum() \
            or (text[j].isupper() and text[j - 1].islower()) \
            or text[j].isdigit() != text[j - 1].isdigit()
        if left and right:
            return True
    return False


# Messages that older versions stored with a full command line or path
# ("Command '['/Users/bob/bin/bash', '-c', 'true']' timed out after 30.0
# seconds", "[Errno 13] Permission denied: '/Users/bob/bin/zsh'") are
# rewritten to the tool-name form today's runner writes, before any
# scrubbing, so sharing an old result keeps its diagnosis.
_STORED_TIMEOUT = re.compile(r"Command '\[(.*?)\]' timed out after ([\d.]+) seconds")
_STORED_EXIT = re.compile(r"Command '\[(.*?)\]' returned non-zero exit status (-?\d+)\.?")
_STORED_OSERROR = re.compile(r"(\[Errno -?\d+\] [^:'\n]+): '([^'\n]*)'")


def _tool_of(args):
    first = re.match(r"\s*'([^'\n]*)'", args)
    return _tool_name(first.group(1) if first else "")


def _tool_name(path):
    base = re.split(r"[\\/]", path.rstrip("\\/"))[-1] if path else ""
    return base if re.sub(r"\.exe\Z", "", base.lower()) in _KEPT_BASENAMES \
        else "<path>"


def _normalize_stored_errors(text):
    text = _STORED_TIMEOUT.sub(lambda m: "{} timed out after {:g} s".format(
        _tool_of(m.group(1)), round(float(m.group(2)), 1)), text)
    text = _STORED_EXIT.sub(lambda m: "{} exited with code {}".format(
        _tool_of(m.group(1)), m.group(2)), text)
    return _STORED_OSERROR.sub(lambda m: "{}: '{}'".format(
        m.group(1), _tool_name(m.group(2))), text)


def _residual_path(text):
    if _PLACEHOLDER_SPLIT.search(text):
        return True
    return any(rx.search(text) for rx in _RESIDUAL_RE) or \
        _scrub_ipv6(text) != text


_SCHEMA_WORDS = None


def _schema_words():
    """Lower-case words of agentperf's own vocabulary.

    Built from the package itself (the benchmark registry and the
    bundled reference profiles), never from the file being shared, so a
    hand-added benchmark id cannot vouch for a name. A user or host name
    made only of these words ("work", "git", "process") is schema, not
    identity, in schema positions; any other name is scrubbed there too.
    """
    global _SCHEMA_WORDS
    if _SCHEMA_WORDS is not None:
        return _SCHEMA_WORDS
    import json
    import pkgutil
    words = set()

    def take(node):
        if isinstance(node, dict):
            for key, value in node.items():
                take(key)
                take(value)
        elif isinstance(node, list):
            for value in node:
                take(value)
        elif isinstance(node, str):
            for sep in (r"\W+", r"[^A-Za-z0-9]+"):
                words.update(w.lower() for w in re.split(sep, node) if w)

    import ast
    from . import benches  # noqa: F401  (registers every benchmark)
    from .benches.process import ENV_POLICY
    from .registry import REGISTRY
    from .runner import PROFILES
    from .util import NODE_ENV_REMOVED, SHELL_ENV_REMOVED
    # Text the benchmarks write into meta (methods, implementations,
    # shell kinds) comes from string literals in their source.
    for info in pkgutil.iter_modules(benches.__path__):
        try:
            tree = ast.parse(pkgutil.get_data(
                "agentperf", "benches/{}.py".format(info.name)).decode("utf-8"))
        except (OSError, SyntaxError, ValueError, AttributeError):
            continue
        docstrings = set()
        for node in ast.walk(tree):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
                docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                take(node.value)
    take(["system", list(PROFILES)])
    for b in REGISTRY:
        take(b.id)
        take(b.describe())
    take([list(_CATEGORIES), ENV_POLICY, list(SHELL_ENV_REMOVED),
          list(NODE_ENV_REMOVED), "sanitized removed NODE_OPTIONS",
          list(_SYSTEM_FIELDS), list(_ENTRY_FIELDS), list(_META_FIELDS),
          list(_TOOL_FIELDS), list(_TOP_FIELDS)])
    try:
        index = json.loads(pkgutil.get_data(
            "agentperf", "references/index.json").decode("utf-8"))
        for entry in index.get("profiles", []):
            take(json.loads(pkgutil.get_data(
                "agentperf", "references/" + entry["file"]).decode("utf-8")))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    _SCHEMA_WORDS = frozenset(words)
    return _SCHEMA_WORDS


def _is_schema_word(name):
    parts = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", name) if w]
    return bool(parts) and all(w in _schema_words() for w in parts)


def _hidden_name(text, names):
    """A known name inside a word that is not agentperf vocabulary
    ("BobLaptop"): the whole-word scrub cannot reach it."""
    words = _schema_words()
    text = unicodedata.normalize("NFC", text).casefold()
    tokens = [t for t in re.split(r"\W+", text) if t and t not in words]
    return any(len(n) >= 3 and n.casefold() in t for t in tokens for n in names)


def _scrub_inside_words(text, names, counter=None):
    """A known name glued into a word that is not agentperf vocabulary
    ("Albuild", "pcbuild", "\u674e\u660eLaptop") is replaced inside that word.
    Vocabulary words ("Balanced") are left whole."""
    words = _schema_words()

    def fix(match):
        word = match.group(0)
        if word.casefold() in words:
            return word
        for name in names:
            if len(name) >= 2:
                word, n = re.subn(re.escape(name), "<name>", word,
                                  flags=re.IGNORECASE)
                if counter is not None:
                    counter[0] += n
        return word
    return re.sub(r"[^\W_]+", fix, text)


def _residual_text(text, names):
    text = unicodedata.normalize("NFC", text)
    lowered = text.casefold()
    return _residual_path(text) or any(
        name.casefold() in lowered if len(name) >= 3
        else _camel_joined(text, name) for name in names)


# --- What share keeps ------------------------------------------------
# share keeps only the fields agentperf writes. Anything else (a note
# typed into the file, a field from another tool) is dropped whole, not
# scrubbed: a filter can only promise to clean text whose shape it
# knows. Each kept field is cleaned by where it sits:
#
# - "schema": schema text and fixed vocabularies (benchmark titles and
#   methods, versions, OS family, architecture, shell and tool file
#   names). Paths
#   and addresses are scrubbed, and so is any user or host name that is
#   not a word of agentperf's own vocabulary (_schema_words); a word
#   still hiding such a name ("BobLaptop") withholds the string. Real
#   schema text holds none of these, so it comes through byte-identical
#   and a host named "process" or "git" cannot move a score.
# - "full": free text (labels, skip reasons, probe errors, profile
#   descriptions) and text a person or vendor can set (power plan, OS
#   version, CPU model, file system type, tool version lines): paths,
#   addresses, and every known user or host name are scrubbed, any other
#   absolute path is replaced whole, and anything still identifying is
#   withheld whole.
# - "hex" (hashes), "day" (timestamps, cut to the day), and numbers
#   must have their shape or are withheld.
#
# Keys are cleaned like schema text.
_SYSTEM_FIELDS = {
    # Fixed vocabularies the probes report (platform names, file system
    # types, power source, virtualization kind).
    "os_family": "schema", "os": "schema", "machine": "schema",
    "python": "schema", "python_impl": "schema", "power_source": "schema",
    "wsl": "schema",
    # Already de-branded before the walk.
    "security_agents": "schema", "antivirus": "schema",
    # Text a person or vendor can set (a power plan name, a custom
    # /etc/os-release, a VM's CPU string, mount options): free text.
    # A FUSE subtype ("fuse.<name>"), a kernel governor, and the
    # virtualization string can be configured too.
    "os_release": "full", "os_version": "full", "cpu_model": "full",
    "fs_type": "full", "governor": "full", "virtualization": "full",
    "fs_options": "full", "power_plan": "full", "spotlight": "full",
    "filevault": "full", "probe_errors": "full",
}
for _key in ("logical_cpus", "physical_cpus", "perf_cores", "efficiency_cores",
             "ram_gb", "disk_total_gb", "disk_free_gb", "load_avg_start",
             "load_avg_end", "low_power_mode", "thermal_warning",
             "time_machine_running", "workdir_in_icloud", "rotational",
             "raid_resync", "workdir_on_windows_drive", "cwd_on_windows_drive",
             "defender_realtime", "windows_search_running", "dev_drive",
             "top_process_max_cpu", "processes_over_80pct"):
    _SYSTEM_FIELDS[_key] = "schema"
_ENTRY_FIELDS = {"category": "schema", "title": "schema", "unit": "schema",
                 "models": "schema", "meta": "meta"}
for _key in ("lower_is_better", "weight", "scored", "ops", "median", "min",
             "max", "p10", "p90", "n", "cv", "mean", "noisy", "samples",
             "run_samples", "run_medians", "runs"):
    _ENTRY_FIELDS[_key] = "schema"
# Methods, implementations, and shell kinds are part of the fingerprint;
# the shell file name is kept when agentperf knows it ("bash",
# "bash.exe"); a custom login shell from $SHELL is free text.
_META_FIELDS = {"method": "schema", "impl": "schema", "shell_kind": "schema",
                "env_policy": "schema", "mode": "schema",
                "matched_files": "schema", "matched_lines": "schema",
                "tls_verified": "schema", "workers": "schema",
                "shell": "exe", "python": "schema"}
_TOOL_FIELDS = ("bash", "claude", "codex", "exec", "git", "grep", "node",
                "powershell", "pwsh", "rg", "shell", "tar")
_TOP_FIELDS = {
    "schema": "schema", "kind": "schema", "suite_version": "schema",
    "package_version": "schema", "profile": "schema", "fingerprint": "hex",
    "started_at": "day", "duration_s": "schema", "label": "full",
    "corpus": "corpus", "system": "system", "tools": "tools",
    "tool_versions": "tool_versions", "benchmarks": "benchmarks",
    "skipped": "skipped", "measurement_env": "measurement_env",
    "scoring_config": "scoring_config", "combined_runs": "runs",
    "merged_runs": "runs", "reference_profile": "refprofile",
    "anonymized": "schema",
}
_OBJECT_FIELDS = {
    "corpus": {"files": "schema", "bytes": "schema", "fixture_hash": "hex"},
    "measurement_env": {"shell_launch": "schema",
                        "node_spawn_diagnostic": "schema"},
    "scoring_config": {"category_weights": "schema"},
    "refprofile": {"name": "schema", "measured": "schema",
                   "description": "full", "anonymized": "schema"},
    "runs": {"started_at": "day", "benchmarks": "schema"},
}
_BENCH_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+\Z")
_HEX = re.compile(r"[0-9a-fA-F]{4,128}\Z")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_CATEGORIES = ("process", "files", "search", "git", "compute",
               "transfer", "ipc", "network", "diag")


def _generic_label(doc: Dict[str, object]) -> str:
    s = doc.get("system", {})
    cpu = str(s.get("cpu_model") or "").strip()
    for noise in ("12th Gen ", "Intel(R) ", "Core(TM) ", "(R)", "(TM)", " CPU"):
        cpu = cpu.replace(noise, "")
    cpu = cpu.split(" @ ")[0].strip() or "unknown CPU"
    fam = {"macos": "macOS", "windows": "Windows", "wsl": "WSL2",
           "linux": "Linux"}.get(str(s.get("os_family", "")), str(s.get("os_family", "")))
    return "{} - {}".format(fam, cpu).strip(" -")


def anonymize(doc: Dict[str, object], label=None) -> Tuple[Dict[str, object], List[str]]:
    """Return (clean copy, list of human-readable changes). Does not mutate input."""
    out = copy.deepcopy(doc)
    names = _candidate_names(out)
    changes: List[str] = []
    old_label = out.get("label")
    out["label"] = label or _generic_label(out)
    if old_label != out["label"]:
        changes.append("label '{}' replaced with '{}'".format(old_label, out["label"]))
    for key in ("run_id",):
        if out.pop(key, None) is not None:
            changes.append("removed {}".format(key))
    system = out.get("system", {})
    # Keep the size of the background load, never the process names.
    top = system.get("top_processes") or []
    if top:
        try:
            cpus = [float(p.get("cpu") or 0) for p in top]
            system["top_process_max_cpu"] = max(cpus)
            system["processes_over_80pct"] = sum(1 for c in cpus if c >= 80)
        except (TypeError, ValueError, AttributeError):
            pass
    for key in _DROP_SYSTEM:
        if system.pop(key, None) is not None:
            changes.append("removed system.{}".format(key))
    # Security products: keep the fact, drop the brand.
    agents = system.get("security_agents") or []
    if agents:
        system["security_agents"] = ["endpoint security agent (name withheld)"]
        changes.append("security product names withheld ({} detected)".format(len(agents)))
    av = system.get("antivirus") or []
    if av:
        system["antivirus"] = [{"name": "antivirus (name withheld)", "active": bool(a.get("active"))}
                               for a in av]
        changes.append("antivirus product names withheld")
    # Tools: file names only, never full paths.
    tools = out.get("tools", {})
    for key, value in list(tools.items()):
        if value:
            tools[key] = os.path.basename(str(value).replace("\\", "/"))
    if tools:
        changes.append("tool paths reduced to file names")
    # Structured path-bearing fields: keep the fact, drop the path. The
    # Spotlight probe embeds the volume path, which can name a client.
    spot = system.get("spotlight")
    if isinstance(spot, str) and ("/" in spot or "\\" in spot):
        system["spotlight"] = ("Indexing enabled" if "Indexing enabled" in spot
                                else "Indexing disabled" if "Indexing disabled" in spot
                                else "unknown")
        changes.append("spotlight volume path generalized")
    # Walk the document by schema position (see "What share keeps").
    # Run ids are removed and timestamps cut to the day at every depth.
    counter = [0]
    withheld = [0]
    nested_ids = [0]
    dropped = [0]
    days = [0]
    # Names that are not agentperf vocabulary are identity wherever
    # they appear, schema positions included.
    strict_names = [n for n in names if not _is_schema_word(n)]

    def withhold():
        withheld[0] += 1
        return "<withheld>"

    def clean_schema(value):
        cleaned = _scrub_text(value, strict_names, counter)
        if _residual_path(cleaned) or _hidden_name(cleaned, strict_names):
            return withhold()
        return cleaned

    def clean_full(value):
        cleaned = _generalize_paths(_scrub_inside_words(
            _scrub_text(_normalize_stored_errors(value), names, counter),
            names, counter))
        # Fail closed: a free-text string still identifying after the
        # scrub is withheld whole instead of passing through.
        if _residual_text(cleaned, names):
            return withhold()
        return cleaned

    def leaf(value, mode):
        if not isinstance(value, str):
            if mode in ("hex", "day") and value is not None:
                return withhold()
            return value
        if mode == "hex":
            return value if _HEX.match(value) else withhold()
        if mode == "day":
            day = value[:10]
            if not _DAY.match(day):
                return withhold()
            if day != value:
                days[0] += 1
            return day
        if mode == "full":
            return clean_full(value)
        if mode == "exe":
            # A shell or tool file name: kept when it is one agentperf
            # knows ("bash", "git.exe"); a custom $SHELL is free text.
            known = re.sub(r"\.exe\Z", "", value.lower()) in _KEPT_BASENAMES
            return clean_schema(value) if known else clean_full(value)
        return clean_schema(value)

    def child_mode(ctx, key):
        """How to clean *key* under *ctx*; None drops it."""
        if ctx == "root":
            return _TOP_FIELDS.get(key)
        if ctx == "system":
            return _SYSTEM_FIELDS.get(key)
        if ctx == "benchmarks":
            return "entry"
        if ctx == "entry":
            return _ENTRY_FIELDS.get(key)
        if ctx == "meta":
            return _META_FIELDS.get(key)
        if ctx in ("tools", "tool_versions"):
            # File names ("git", "bash.exe") are a fixed vocabulary; a
            # version line is vendor text and can name a build host.
            if key not in _TOOL_FIELDS:
                return None
            return "exe" if ctx == "tools" else "full"
        if ctx == "skipped":
            return "full"
        if ctx in _OBJECT_FIELDS:
            return _OBJECT_FIELDS[ctx].get(key)
        return ctx  # schema / full: nested values keep their mode

    def walk(node, ctx):
        if isinstance(node, dict):
            if ctx in ("hex", "day"):
                return withhold()
            out_dict = {}
            for k, v in node.items():
                if k == "run_id":
                    nested_ids[0] += 1
                    continue
                mode = child_mode(ctx, k) if isinstance(k, str) else None
                if mode is None:
                    dropped[0] += 1
                    continue
                key = clean_schema(k)
                if ctx == "benchmarks" and (key != k or not _BENCH_ID.match(k)):
                    # Not a benchmark agentperf defines (or one that
                    # names a person): drop it rather than rename it.
                    dropped[0] += 1
                    continue
                if key in out_dict:
                    # Two keys scrubbed to one placeholder stay distinct.
                    n = 2
                    while "{} ({})".format(key, n) in out_dict:
                        n += 1
                    key = "{} ({})".format(key, n)
                out_dict[key] = walk(v, mode)
            return out_dict
        if isinstance(node, list):
            if ctx == "runs":
                return [walk(v, ctx) if isinstance(v, dict) else withhold()
                        for v in node]
            return [walk(v, ctx) for v in node]
        if ctx in ("root", "system", "benchmarks", "entry", "meta", "tools",
                   "tool_versions", "skipped") or ctx in _OBJECT_FIELDS:
            return withhold() if isinstance(node, str) else node
        return leaf(node, ctx)

    out = walk(out, "root")
    if days[0]:
        changes.append("cut {} timestamp(s) to the day".format(days[0]))
    if dropped[0]:
        changes.append("dropped {} field(s) agentperf does not write".format(
            dropped[0]))
    if withheld[0]:
        changes.append("withheld {} string(s) that could not be scrubbed "
                       "clean".format(withheld[0]))
    if nested_ids[0]:
        changes.append("removed {} nested run id(s) and cut nested timestamps "
                       "to the day".format(nested_ids[0]))
    if counter[0]:
        changes.append("scrubbed {} private string(s) (home paths, IP/MAC addresses, "
                       "user/host names) in kept fields".format(counter[0]))
    out["anonymized"] = True
    # The schema-aware scrub never touches strings the fingerprint
    # covers (methods, titles, definitions), so the fingerprint stays
    # as it was; recompute anyway as a safety net so the shared file's
    # fingerprint always matches its contents.
    if out.get("fingerprint") and out.get("benchmarks"):
        from . import scoring
        fresh = scoring.fingerprint(out)
        if fresh != out["fingerprint"]:
            out["fingerprint"] = fresh
            changes.append("measurement/scoring fingerprint regenerated for the scrubbed copy")
    return out, changes
