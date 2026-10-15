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
"""

from __future__ import annotations

import copy
import ipaddress
import os
import re
from typing import Dict, List, Tuple

# Keys dropped from system outright.
_DROP_SYSTEM = ("hostname", "workdir", "cwd", "top_processes", "physical_disks",
                "fs_device", "fs_mount", "model_id")

_HOME_CAPTURE = [
    # POSIX user folders can contain spaces and apostrophes too
    # ("/Users/Alice Smith", "/Users/O'Brien"). Matching is
    # case-insensitive: "/USERS/..." and "c:/users/..." are the same
    # paths on the filesystems that produce them.
    re.compile(r"/Users/([^\n\"/]+?)(?:/|$)", re.IGNORECASE),
    re.compile(r"/home/([^\n\"/]+?)(?:/|$)", re.IGNORECASE),
    # A Windows user folder can itself contain spaces ("Alice Smith").
    re.compile(r"[A-Za-z]:\\Users\\([^\\\"\n]+?)(?:\\|$)", re.IGNORECASE),
    re.compile(r"[A-Za-z]:/Users/([^/\"\n]+)", re.IGNORECASE),
    re.compile(r"[A-Za-z]:[\\/]Documents and Settings[\\/]([^\\/\"\n]+?)"
               r"(?:[\\/]|$)", re.IGNORECASE),
    re.compile(r"\\\\([^\\\s\"'/]+)"),          # UNC server name
    re.compile(r"(?<![\w:])//([^\s\"'/]+)/"),    # //server/share
    # ~user/... with any user name ("~_bob", "~123bob", accented
    # letters), and a bare ~user that starts with a letter.
    re.compile(r"~([^\s/\\\"~<][^\s/\\\"~]*)[/\\]"),
    re.compile(r"(?<![\w~])~([^\W\d][\w.-]*)"),
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
    (re.compile(r"(?<![\w~])~[^\W\d][\w.-]*"), "~<user>"),
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
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"), "<mac>"),
    (re.compile(r"\b[\w.-]+\.ts\.net\b", re.IGNORECASE), "<host>"),
]
# Candidate IPv6 tokens; each is validated with ipaddress so the *whole*
# address (including its network prefix) is replaced, never a suffix.
_IPV6_CANDIDATE = re.compile(
    r"(?<![0-9A-Fa-f:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}"
    r"(?:\.\d{1,3}){0,3}(?![0-9A-Fa-f:.])|::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f:]*")


def _scrub_ipv6(value: str, counter=None) -> str:
    def repl(match):
        token = match.group(0)
        try:
            addr = ipaddress.ip_address(token)
        except ValueError:
            return token
        if not isinstance(addr, ipaddress.IPv6Address):
            return token
        if counter is not None:
            counter[0] += 1
        return "<ip>"
    return _IPV6_CANDIDATE.sub(repl, value)
_NOT_NAMES = {"<user>", "shared", "public", "default", "users", "home"}


def _candidate_names(doc) -> List[str]:
    """User/host names worth scrubbing as bare words: home-path users + hostname."""
    names: List[str] = []

    def add(name):
        name = (name or "").strip()
        # Two-letter names ("Al", a host called "pc") are scrubbed as
        # whole words; one letter is too generic to be a name.
        if len(name) >= 2 and name.lower() not in _NOT_NAMES and name not in names:
            names.append(name)
        # A captured "Alice Smith" also makes bare "Smith" identifying.
        for part in name.split():
            if len(part) >= 3 and part.lower() not in _NOT_NAMES and part not in names:
                names.append(part)
        # So does the fragment of a hyphenated host or folder name
        # ("alicez" in "alicez-mac.local"); short fragments are too
        # generic to scrub as bare words.
        for part in re.split(r"[^A-Za-z0-9]+", name):
            if len(part) >= 4 and part.lower() not in _NOT_NAMES \
                    and part not in names:
                names.append(part)

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
                    add(match.group(1))

    walk(doc)
    hostname = str((doc.get("system") or {}).get("hostname") or "")
    add(hostname)
    add(hostname.split(".")[0])
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
    value = _scrub_paths(value, counter)
    for name in names:
        # Case variants of a known name are the same name ("ALICE").
        value, n = re.subn(r"\b" + re.escape(name) + r"\b", "<name>", value,
                           flags=re.IGNORECASE)
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
    re.compile(r"[A-Za-z]:[\\/]Documents and Settings[\\/]", re.IGNORECASE),
    re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[\w.-]+\.ts\.net\b", re.IGNORECASE),
]


def _residual_path(text):
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
            words.update(w.lower() for w in re.split(r"\W+", node) if w)

    from . import benches  # noqa: F401  (registers every benchmark)
    from .registry import REGISTRY
    for b in REGISTRY:
        take(b.id)
        take(b.describe())
    take(list(_CATEGORIES))
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
    parts = [w.lower() for w in re.split(r"\W+", name) if w]
    return bool(parts) and all(w in _schema_words() for w in parts)


def _residual_text(text, names):
    lowered = text.lower()
    return _residual_path(text) or any(
        len(name) >= 3 and name.lower() in lowered for name in names)


# --- Schema awareness -------------------------------------------------
# Anonymization must never rewrite the result schema itself. A hostname
# of "work" or a username of "git" must leave benchmark ids, schema
# keys, categories, units, method strings, and scoring config byte-
# identical, or sharing would move scores (round-8 F1). The walk in
# anonymize() therefore classifies every string by where it sits:
#
# - "keep" and "path": schema text and known generic facts (benchmark
#   definitions and methods, the known corpus, scoring, and measurement
#   fields, versions, CPU models, OS names, tool versions). Paths and
#   addresses are scrubbed, and so is any user or host name that is not
#   a word of agentperf's own vocabulary (_schema_words). Real schema
#   text holds neither, so it comes through byte-identical.
# - "full": free text that can hold identifying content (labels, skip
#   reasons, unknown fields, notes): paths, addresses, and every known
#   user/host name are scrubbed, and anything still identifying is
#   withheld whole.
#
# Dict keys are scrubbed like schema text, so "benchmarks" survives a
# hostname of "bench" while a path or a real name used as a key is
# still generalized.
_BENCH_KEEP_KEYS = ("category", "title", "unit", "models")
_META_KEEP_KEYS = ("method", "impl", "shell_kind", "env_policy", "shell",
                   "python", "mode")
# Generic system facts whose values explain the measurements and must
# survive verbatim (modulo any path they might embed).
_SYSTEM_PATH_KEYS = (
    "os_family", "os", "os_release", "os_version", "machine",
    "python", "python_impl", "cpu_model", "model_id", "fs_type",
    "fs_options", "power_source", "power_plan", "governor", "spotlight",
    "filevault", "virtualization", "wsl",
)
_TOP_KEEP_KEYS = ("schema", "kind", "suite_version", "package_version",
                  "profile", "fingerprint")
# Top-level objects the tool writes whole. Their known children are
# kept verbatim (the shell policy text is classified, the weights and
# corpus facts feed scoring); unknown children are scrubbed like any
# other free text.
_STRUCT_KEEP_KEYS = {
    "corpus": ("files", "bytes", "fixture_hash"),
    "measurement_env": ("shell_launch", "node_spawn_diagnostic"),
    "scoring_config": ("category_weights",),
}
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
    # Scrub the strings that can hold identifying content, schema-
    # aware: a user or host name that is also agentperf vocabulary is
    # left alone in schema positions, so it cannot move a score. Run ids
    # are removed and timestamps are cut to the day at every depth,
    # including the top level and combined_runs/merged_runs.
    counter = [0]
    withheld = [0]
    nested_ids = [0]
    # Whole strings treated as schema wherever they appear (benchmark
    # ids, also listed under merged_runs; category names; units), so a
    # host named "files" leaves them alone. They still go through the
    # schema scrub, so a hand-added id cannot shelter a real name.
    protected_strings = set(_CATEGORIES)
    benches0 = out.get("benchmarks")
    if isinstance(benches0, dict):
        protected_strings.update(
            k for k in benches0 if isinstance(k, str))
        for entry0 in benches0.values():
            if isinstance(entry0, dict):
                for field in ("category", "unit"):
                    if isinstance(entry0.get(field), str):
                        protected_strings.add(entry0[field])

    # Names that are not agentperf vocabulary are identity wherever
    # they appear, schema positions included.
    strict_names = [n for n in names if not _is_schema_word(n)]

    def clean_schema(value):
        # Schema text (kept fields, path fields, keys, benchmark ids)
        # gets the full path and address scrub and loses every name
        # that is not a vocabulary word. Real schema text never holds
        # either, so it comes through byte-identical; anything typed
        # in by hand is scrubbed or withheld like free text.
        cleaned = _scrub_text(value, strict_names, counter)
        if _residual_path(cleaned):
            withheld[0] += 1
            return "<withheld>"
        return cleaned

    def clean_string(value, mode):
        if mode in ("keep", "path") or value in protected_strings:
            return clean_schema(value)
        cleaned = _scrub_text(value, names, counter)
        # Fail closed: a free-text string still identifying after the
        # scrub is withheld whole instead of passing through.
        if _residual_text(cleaned, names):
            withheld[0] += 1
            return "<withheld>"
        return cleaned

    def clean_key(key):
        if not isinstance(key, str):
            return key
        return clean_schema(key)

    def child_ctx(ctx, key):
        if ctx == "root":
            if key == "benchmarks":
                return "benchmarks"
            if key == "system":
                return "system"
            if key in _TOP_KEEP_KEYS:
                return "keep"
            if key in _STRUCT_KEEP_KEYS:
                return key
            if key in ("tools", "tool_versions"):
                return "path"
            if key == "reference_profile":
                return "refprofile"
            return "full"
        if ctx == "benchmarks":
            return "entry"
        if ctx == "entry":
            if key in _BENCH_KEEP_KEYS:
                return "keep"
            if key == "meta":
                return "meta"
            return "full"
        if ctx == "meta":
            return "keep" if key in _META_KEEP_KEYS else "full"
        if ctx == "system":
            if key in _SYSTEM_PATH_KEYS:
                return "path"
            # Already de-branded above; keep the fixed replacements.
            if key in ("security_agents", "antivirus"):
                return "keep"
            return "full"
        if ctx == "refprofile":
            return "keep" if key in ("name", "measured") else "full"
        if ctx in _STRUCT_KEEP_KEYS:
            # Only the children the tool writes are structural; anything
            # else in these objects is unknown text and gets scrubbed.
            return "keep" if key in _STRUCT_KEEP_KEYS[ctx] else "full"
        return ctx  # keep / path / full propagate into nested values

    def scrub(node, ctx):
        if isinstance(node, dict):
            out_dict = {}
            for k, v in node.items():
                if k == "run_id":
                    nested_ids[0] += 1
                    continue
                # Keys carry text too (a path or a name can be a key):
                # scrubbed as schema text, so vocabulary keys survive.
                key = clean_key(k)
                if key in out_dict:
                    # Two keys scrubbed to one placeholder stay distinct.
                    n = 2
                    while "{} ({})".format(key, n) in out_dict:
                        n += 1
                    key = "{} ({})".format(key, n)
                if k == "started_at" and isinstance(v, str):
                    # A timestamp is cut to the day; anything else under
                    # this name is free text and is scrubbed as such.
                    day = v[:10]
                    out_dict[key] = day if _DAY.match(day) \
                        else clean_string(v, "full")
                    continue
                out_dict[key] = scrub(v, child_ctx(ctx, k))
            return out_dict
        if isinstance(node, list):
            return [scrub(v, ctx) for v in node]
        if isinstance(node, str):
            mode = ctx if ctx in ("keep", "path") else "full"
            return clean_string(node, mode)
        return node

    out = scrub(out, "root")
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
