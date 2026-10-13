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
import os
import re
from typing import Dict, List, Tuple

# Keys dropped from system outright.
_DROP_SYSTEM = ("hostname", "workdir", "top_processes", "physical_disks",
                "fs_device", "fs_mount", "model_id")

_HOME_CAPTURE = [
    re.compile(r"/Users/([^/\"'\\\s]+)"),
    re.compile(r"/home/([^/\"'\\\s]+)"),
    re.compile(r"[A-Za-z]:\\Users\\([^\\\"'\s]+)"),
    re.compile(r"[A-Za-z]:/Users/([^/\"'\s]+)"),
]
_HOME_RE = [
    (re.compile(r"/Users/[^/\"'\\\s]+"), "/Users/<user>"),
    (re.compile(r"/home/[^/\"'\\\s]+"), "/home/<user>"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\"'\s]+"), r"C:\Users\<user>"),
    (re.compile(r"[A-Za-z]:/Users/[^/\"'\s]+"), "C:/Users/<user>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
                r"|\b[0-9a-fA-F]{0,4}::[0-9a-fA-F:]+\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b"), "<mac>"),
    (re.compile(r"\b[\w.-]+\.ts\.net\b"), "<host>"),
]
_NOT_NAMES = {"<user>", "shared", "public", "default", "users", "home"}


def _candidate_names(doc) -> List[str]:
    """User/host names worth scrubbing as bare words: home-path users + hostname."""
    names: List[str] = []

    def add(name):
        name = (name or "").strip()
        if len(name) >= 3 and name.lower() not in _NOT_NAMES and name not in names:
            names.append(name)

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
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
    for rx, repl in _HOME_RE:
        value, n = rx.subn(lambda m, r=repl: r, value)
        if counter is not None:
            counter[0] += n
    for name in names:
        value, n = re.subn(r"\b" + re.escape(name) + r"\b", "<name>", value)
        if counter is not None:
            counter[0] += n
    return value


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
    # Scrub home paths, addresses, and known user/host names in kept strings.
    counter = [0]

    def scrub(node):
        if isinstance(node, dict):
            return {k: scrub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return _scrub_text(node, names, counter)

    out = scrub(out)
    if counter[0]:
        changes.append("scrubbed {} private string(s) (home paths, IP/MAC addresses, "
                       "user/host names) in kept fields".format(counter[0]))
    out["anonymized"] = True
    return out, changes
