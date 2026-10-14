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
    # POSIX user folders can contain spaces too ("/Users/Alice Smith").
    re.compile(r"/Users/([^\n\"'/]+?)(?:/|$)"),
    re.compile(r"/home/([^\n\"'/]+?)(?:/|$)"),
    # A Windows user folder can itself contain spaces ("Alice Smith").
    re.compile(r"[A-Za-z]:\\Users\\([^\\\"'\n]+?)(?:\\|$)"),
    re.compile(r"[A-Za-z]:/Users/([^/\"'\s]+)"),
    re.compile(r"\\\\([^\\\s\"'/]+)"),          # UNC server name
    re.compile(r"(?<![\w:])//([^\s\"'/]+)/"),    # //server/share
    re.compile(r"~([A-Za-z][\w.-]*)[/\\]"),      # ~user/...
]
# Home paths: the subpath under a home directory is private too (client
# names, project names), so the whole path token is generalized. Volume
# names under /Volumes can name clients or projects as well. Path
# segments may contain spaces ("/Volumes/Client Merger Documents",
# r"C:\Users\Alice Smith\..."), so these patterns must not stop at a
# space inside a path. They run to the next quote or line end instead,
# which can over-scrub words that follow a path in free text; for a
# privacy filter that is the safe direction.
_HOME_RE = [
    # UNC paths name a server, a share, and a private folder tree,
    # in backslash or forward-slash form.
    (re.compile(r"\\\\[^\n\"']+"), r"\\<host>\<share>"),
    (re.compile(r"(?<![\w:])//[^\s\"'/]+/[^\n\"']*"), "//<host>/<share>"),
    # Tilde paths are home paths: "~alice/Secret", "~/Client Plans".
    (re.compile(r"~[A-Za-z][\w.-]*[/\\][^\n\"']*"), "~<user>"),
    (re.compile(r"~[/\\][^\n\"']*"), "~<user>"),
    (re.compile(r"(?<![\w])~[A-Za-z][\w.-]*"), "~<user>"),
    # The user folder itself may contain spaces, so the whole token to
    # the next quote or line end is generalized, as with Windows below.
    (re.compile(r"/Users/[^\n\"']+"), "/Users/<user>"),
    (re.compile(r"/home/[^\n\"']+"), "/home/<user>"),
    (re.compile(r"/root(?:/[^\n\"']*)?"), "/root/<user>"),
    (re.compile(r"/Volumes/[^\n\":]+:"), "/Volumes/<volume>:"),
    (re.compile(r"/Volumes/[^\n\"']+"), "/Volumes/<volume>"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\n\"']+"), r"C:\Users\<user>"),
    (re.compile(r"[A-Za-z]:\\Documents and Settings\\[^\n\"']+"), r"C:\Users\<user>"),
    (re.compile(r"[A-Za-z]:/Users/[^\n\"']+"), "C:/Users/<user>"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "<email>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"), "<mac>"),
    (re.compile(r"\b[\w.-]+\.ts\.net\b"), "<host>"),
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
        if len(name) >= 3 and name.lower() not in _NOT_NAMES and name not in names:
            names.append(name)
        # A captured "Alice Smith" also makes bare "Smith" identifying.
        for part in name.split():
            if len(part) >= 3 and part.lower() not in _NOT_NAMES and part not in names:
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


def _scrub_text(value, names=(), counter=None):
    if not isinstance(value, str):
        return value
    value = _scrub_ipv6(value, counter)
    for rx, repl in _HOME_RE:
        value, n = rx.subn(lambda m, r=repl: r, value)
        if counter is not None:
            counter[0] += n
    for name in names:
        # Case variants of a known name are the same name ("ALICE").
        value, n = re.subn(r"\b" + re.escape(name) + r"\b", "<name>", value,
                           flags=re.IGNORECASE)
        if counter is not None:
            counter[0] += n
    return value


# What must never survive a scrub, placeholder forms excepted. A string
# still matching one of these after scrubbing is withheld whole: fields
# the scrubber cannot clean are dropped, never passed through.
_RESIDUAL_RE = [
    re.compile(r"\\\\(?!<host>)"),
    re.compile(r"(?<![\w:])//(?!<host>)[^\s\"'/]+/"),
    re.compile(r"~[A-Za-z][\w.-]*[/\\]"),
    re.compile(r"~[/\\]"),
    re.compile(r"/Users/(?!<user>)"),
    re.compile(r"/home/(?!<user>)"),
    re.compile(r"/root/(?!<user>)"),
    re.compile(r"/Volumes/(?!<volume>)"),
    re.compile(r"[A-Za-z]:\\Users\\(?!<user>)"),
    re.compile(r"[A-Za-z]:/Users/(?!<user>)"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b[\w.-]+\.ts\.net\b"),
]


def _withhold_residuals(node, names, counter):
    """Replace any string that still looks identifying with <withheld>."""
    if isinstance(node, dict):
        return {_withhold_residuals(k, names, counter):
                _withhold_residuals(v, names, counter)
                for k, v in node.items()}
    if isinstance(node, list):
        return [_withhold_residuals(v, names, counter) for v in node]
    if not isinstance(node, str):
        return node
    lowered = node.lower()
    bad = any(rx.search(node) for rx in _RESIDUAL_RE) or \
        any(len(name) >= 3 and name.lower() in lowered for name in names) or \
        _scrub_ipv6(node) != node
    if bad:
        counter[0] += 1
        return "<withheld>"
    return node


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
    if out.get("started_at"):
        out["started_at"] = str(out["started_at"])[:10]  # keep the day, drop the clock
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
    # Scrub home paths, addresses, and known user/host names in kept strings.
    # Run ids are removed and timestamps are cut to the day at every depth,
    # including inside combined_runs/merged_runs.
    counter = [0]
    nested_ids = [0]

    def scrub(node):
        if isinstance(node, dict):
            out_dict = {}
            for k, v in node.items():
                if k == "run_id":
                    nested_ids[0] += 1
                    continue
                # Keys carry text too (a path can be a key); scrub them.
                clean_key = _scrub_text(k, names, counter)
                if k == "started_at" and isinstance(v, str):
                    out_dict[clean_key] = v[:10]
                    continue
                out_dict[clean_key] = scrub(v)
            return out_dict
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return _scrub_text(node, names, counter)

    out = scrub(out)
    # Fail closed: anything still identifying after the scrub is
    # withheld whole instead of passing through.
    withheld = [0]
    out = _withhold_residuals(out, names, withheld)
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
    # Scrubbing can change strings the fingerprint covers (methods,
    # titles); regenerate it under the recorded config so the shared
    # file's fingerprint still matches its contents.
    if out.get("fingerprint") and out.get("benchmarks"):
        from . import scoring
        fresh = scoring.fingerprint(out)
        if fresh != out["fingerprint"]:
            out["fingerprint"] = fresh
            changes.append("measurement/scoring fingerprint regenerated for the scrubbed copy")
    return out, changes
