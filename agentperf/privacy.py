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

_HOME_RE = [
    (re.compile(r"/Users/[^/\"'\\\s]+"), "/Users/<user>"),
    (re.compile(r"/home/[^/\"'\\\s]+"), "/home/<user>"),
    (re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\"'\s]+"), r"C:\Users\<user>"),
    (re.compile(r"[A-Za-z]:/Users/[^/\"'\s]+"), "C:/Users/<user>"),
]


def _scrub_text(value):
    if not isinstance(value, str):
        return value
    for rx, repl in _HOME_RE:
        value = rx.sub(lambda m, r=repl: r, value)
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
    # Scrub any remaining home paths in every string field we keep.
    def scrub(node):
        if isinstance(node, dict):
            return {k: scrub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        return _scrub_text(node)

    out = scrub(out)
    out["anonymized"] = True
    return out, changes
