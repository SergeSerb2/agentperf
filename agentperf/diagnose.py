"""Explain WHY a machine scores the way it does.

Two layers, both honest about certainty:

1. **Attribution (exact).** AgentMark is a weighted geometric mean, so the
   log score splits exactly into per-category and per-test contributions.
   For every test we report its ratio to the reference and how many
   AgentMark points it costs (or earns). For any set of tests we can
   compute the counterfactual score if those tests matched the reference.
   That number is a *counterfactual estimate* for a fix, never a promise
   and not a ceiling: a real fix can land below it, or past it.

2. **Causes (evidence, labelled).** Each cause cites the probe or the
   measurement pattern that supports it and carries a confidence:
   ``observed`` (a probe directly shows it), ``likely`` (probe plus a
   matching measurement pattern), or ``possible`` (pattern only).
   A cause is never stated without its evidence list.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Set

from . import scoring
from .registry import CATEGORY_TITLES, CATEGORY_WEIGHTS
from .util import geomean, version_tuple


def _median(result, bench_id):
    entry = result["benchmarks"].get(bench_id)
    return float(entry["median"]) if entry else None


def ratios(result, reference) -> Dict[str, float]:
    """Per-test speed ratio vs reference (2.0 = twice as fast)."""
    out = {}
    for bid, entry in result["benchmarks"].items():
        ref = reference["benchmarks"].get(bid)
        if not ref or not entry.get("scored") or not ref.get("scored"):
            continue
        v, r = float(entry["median"]), float(ref["median"])
        if v <= 0 or r <= 0:
            continue
        out[bid] = r / v if entry["lower_is_better"] else v / r
    return out


def _agentmark_from(rr: Dict[str, float], result) -> Optional[float]:
    by_cat: Dict[str, List[str]] = {}
    for bid in rr:
        cat = result["benchmarks"][bid]["category"]
        if cat in CATEGORY_WEIGHTS:
            by_cat.setdefault(cat, []).append(bid)
    if not by_cat:
        return None
    cat_r = {c: geomean([rr[i] for i in ids],
                         [float(result["benchmarks"][i]["weight"]) for i in ids])
             for c, ids in by_cat.items()}
    return 1000 * geomean([cat_r[c] for c in cat_r], [CATEGORY_WEIGHTS[c] for c in cat_r])


def gain_if_fixed(result, reference, bench_ids) -> Dict[str, Optional[float]]:
    """Counterfactual: AgentMark if the given tests matched the reference.

    Tests already faster than the reference are left alone. The result is a
    counterfactual estimate, not a ceiling: a real fix may reach reference
    speed, fall short of it, or pass it.
    """
    rr = ratios(result, reference)
    base = _agentmark_from(rr, result)
    fixed = dict(rr)
    for bid in bench_ids:
        if bid in fixed and fixed[bid] < 1.0:
            fixed[bid] = 1.0
    new = _agentmark_from(fixed, result)
    pct = (new / base - 1.0) * 100.0 if base and new else None
    return {"base": base, "if_fixed": new, "gain_pct": pct,
            "gain_points": (new - base) if base and new else None}


def attribution(result, reference) -> Dict[str, object]:
    """Exact log-space split of the score into categories and tests."""
    rr = ratios(result, reference)
    sc = scoring.score(result, reference)
    cats: Dict[str, object] = {}
    for cat, title in CATEGORY_TITLES.items():
        ids = [b for b in rr if result["benchmarks"][b]["category"] == cat]
        if not ids:
            continue
        tests = []
        for bid in sorted(ids, key=lambda b: rr[b]):
            e, ref_e = result["benchmarks"][bid], reference["benchmarks"][bid]
            gain = gain_if_fixed(result, reference, [bid])
            tests.append({
                "id": bid, "title": e.get("title", bid), "unit": e["unit"],
                "value": float(e["median"]), "reference": float(ref_e["median"]),
                "ratio": rr[bid], "score": 1000 * rr[bid],
                "noisy": bool(e.get("noisy")), "cv": float(e.get("cv") or 0),
                "gain_points": gain["gain_points"] or 0.0,
                "models": e.get("models", ""),
            })
        cats[cat] = {
            "title": title, "weight": CATEGORY_WEIGHTS.get(cat),
            "score": sc["categories"].get(cat), "tests": tests,
            "gain_points": (gain_if_fixed(result, reference, ids)["gain_points"] or 0.0),
        }
    drag = sorted((t for c in cats.values() for t in c["tests"] if t["ratio"] < 1.0),
                  key=lambda t: -t["gain_points"])
    return {"score": sc, "categories": cats, "biggest_drags": drag[:6]}


# ------------------------------------------------------------------ causes

MINOR_GAIN_POINTS = 5.0


def partition_causes(items):
    """Split ranked causes into (major, minor).

    A scored cause whose estimated gain is under MINOR_GAIN_POINTS is
    minor: real, but not worth a headline card. Causes whose gain is not
    estimated stay major.
    """
    major, minor = [], []
    for c in items:
        estimated = c.get("gain_estimated", bool(c.get("affected")))
        if estimated and c.get("affected") and (c.get("gain_points") or 0.0) < MINOR_GAIN_POINTS:
            minor.append(c)
        else:
            major.append(c)
    return major, minor


def _workdir_on_windows_drive(s) -> bool:
    """True when the measured work directory is /mnt/* in WSL (score affected)."""
    if s.get("workdir_on_windows_drive"):
        return True
    if not (s.get("os_family") == "wsl" or s.get("wsl")):
        return False
    return str(s.get("workdir") or "").startswith("/mnt/")


def _cwd_on_windows_drive(s) -> bool:
    """True when only the launch directory is /mnt/* (score not affected)."""
    if _workdir_on_windows_drive(s):
        return False
    if s.get("cwd_on_windows_drive"):
        return True
    if not (s.get("os_family") == "wsl" or s.get("wsl")):
        return False
    return str(s.get("cwd") or "").startswith("/mnt/")


def _cause(topic, confidence, summary, evidence, fix, affected, result, reference,
           priority=0, affected_text=None, estimate_gain=True):
    gain = gain_if_fixed(result, reference, affected) if (affected and estimate_gain) else {}
    estimated = bool(affected and estimate_gain)
    return {
        "topic": topic, "confidence": confidence, "summary": summary,
        "evidence": evidence, "fix": fix, "affected": sorted(set(affected)),
        "affected_text": affected_text or (", ".join(sorted(set(affected))) if affected
                                           else "not tied to scored tests"),
        "gain_estimated": estimated,
        "gain_points": (gain.get("gain_points") or 0.0) if estimated else None,
        "gain_pct": (gain.get("gain_pct") or 0.0) if estimated else None,
        "agentmark_if_fixed": gain.get("if_fixed") if estimated else None,
        "priority": priority,
    }


def causes(result, reference) -> List[Dict[str, object]]:
    """Ranked likely causes with evidence. Empty list = nothing stands out."""
    s = result.get("system", {})
    rr = ratios(result, reference)
    out: List[Dict[str, object]] = []

    def slow(ids, bar=0.7):
        got = [rr[i] for i in ids if i in rr]
        return got and (sum(got) / len(got)) < bar

    process_ids = [b for b in rr if b.startswith("process.")]
    file_ids = [b for b in rr if b.startswith("files.")]
    compute_ok = not slow(["compute.sha256", "compute.zlib"], bar=0.8)

    # Antivirus / endpoint security: probe + process/file pattern.
    active_av = [a for a in (s.get("antivirus") or []) if a.get("active")]
    agents = s.get("security_agents") or []
    if (active_av or agents or s.get("defender_realtime")) and (slow(process_ids) or slow(file_ids)):
        ev = []
        if active_av:
            ev.append("Probe: real-time antivirus scanning is active.")
        if agents:
            ev.append("Probe: endpoint security agent detected: {}.".format(", ".join(agents)))
        if s.get("defender_realtime"):
            ev.append("Probe: Microsoft Defender real-time protection is on"
                      + (" and the work drive is not a Dev Drive." if not s.get("dev_drive") else "."))
        shell = _median(result, "process.shell")
        ref_shell = _median(reference, "process.shell")
        if shell and ref_shell:
            if compute_ok:
                ev.append("Pattern: a shell start costs {:.1f} ms here vs {:.1f} ms on the reference, "
                          "while raw compute stays competitive. Scanners inspect every process "
                          "start and file write; pure CPU work does not pass through them.".format(shell, ref_shell))
            else:
                ev.append("Pattern: a shell start costs {:.1f} ms here vs {:.1f} ms on the reference. "
                          "Raw compute is slow here too, so scanning is at most part of the gap; "
                          "the CPU and its tools differ as well.".format(shell, ref_shell))
        out.append(_cause(
            "Antivirus / endpoint security scanning", "likely",
            "Security scanning taxes every process start and small-file operation. "
            "On one measured PC (October 2026), native Windows with third-party "
            "real-time antivirus and the Windows Search indexer scored far "
            "below WSL2 on the same hardware (see the "
            "bundled profiles). Scanning is part of that gap. It was not measured "
            "on its own: that PC was never run with scanning off, and the native "
            "side also differed in shell (Git Bash) and Python version.",
            ev,
            ["Exclude your code folders, package caches (npm, pip, cargo), and ~/.agentperf from real-time scanning.",
             "On Windows, put code on a Dev Drive (ReFS) so Defender uses performance mode, or run agents in WSL2 with repos on the Linux filesystem.",
             "On a managed machine, ask IT for a developer-folder exclusion instead of disabling protection."],
            process_ids + file_ids, result, reference))
    elif slow(process_ids) and slow(file_ids) and compute_ok:
        out.append(_cause(
            "Process and file overhead (OS or security layer)", "possible",
            "Process starts and small files are slow while raw compute is not. "
            "That pattern points at per-process/per-file overhead (security filters, "
            "OS process model, sync or indexing hooks), not at the CPU.",
            ["Pattern: process and file tests trail the reference; compute tests do not.",
             "No security product was detected by name, so treat this as a pattern, not a named cause."],
            ["Rule out security software, indexing, and synced folders on code folders; what remains is the OS's own per-process cost.",
             "Exclude code folders from OS indexing (Spotlight / Windows Search).",
             "Keep repos off synced folders (iCloud, OneDrive) and off /mnt/<drive> in WSL."],
            process_ids + file_ids, result, reference))

    # Native Windows: the measured same-hardware WSL2 gap is the headline win,
    # but only claim it when this machine actually shows the slow pattern. A
    # tuned native setup (Dev Drive, AV exclusions) can be fine as it is.
    if s.get("os_family") == "windows" and (slow(process_ids) or slow(file_ids)):
        ev = ["Probe: this run is native Windows (os_family=windows), not WSL2.",
              "Pattern: process starts and/or small files trail the WSL2 "
              "reference on this machine.",
              "Measured on the bundled reference hardware (i7-12700K, October "
              "2026): the native-Windows profile (third-party real-time "
              "antivirus and the Windows Search indexer active, shell "
              "commands through Git Bash) scored far "
              "below WSL2 on the same PC. That gap is everything that native "
              "setup added; it was not split into parts, and no run with "
              "scanning off exists. One PC, with those settings; your gap "
              "may be smaller."]
        shell = _median(result, "process.shell")
        ref_shell = _median(reference, "process.shell")
        if shell and ref_shell and shell > 2 * ref_shell:
            ev.append("Pattern: a shell start costs {:.1f} ms here vs {:.1f} ms on the "
                      "WSL2 reference.".format(shell, ref_shell))
        out.append(_cause(
            "Run agents in WSL2 with repos on the Linux filesystem", "likely",
            "This machine shows the slow native pattern: per-process and "
            "per-file costs are a large part of the limit. On the one PC "
            "where both sides were measured, WSL2 on the same hardware "
            "scored far above the native-Windows setup described in the "
            "evidence - the largest single change agentperf has measured, "
            "with those conditions attached. Agents start processes and "
            "touch small files all day; WSL2 does both at Linux cost. Keep "
            "repos on the Linux filesystem: under /mnt/c every file "
            "operation crosses to NTFS and small-file work gets slower "
            "than native Windows.",
            ev,
            ["Install WSL2 (wsl --install) and run the agent inside it.",
             "Clone repos inside the Linux filesystem (for example ~/code), never under /mnt/c.",
             "Open them from Windows via \\\\wsl$ only for occasional file access.",
             "If agents must stay native, use a Dev Drive and antivirus exclusions for code folders."],
            process_ids + file_ids + [b for b in rr if b.startswith(("git.", "search.", "transfer."))],
            result, reference, priority=90))

    # Workdir location. A workdir on /mnt/ means the disk numbers measure
    # NTFS through 9P: the score itself is compromised. A launch directory
    # there (workdir elsewhere) does not touch the score at all.
    if _workdir_on_windows_drive(s):
        out.append(_cause(
            "Repository on a Windows drive from WSL (/mnt/...)", "observed",
            "The benchmark work directory is under /mnt/. In WSL2 every file "
            "operation crosses to Windows NTFS through 9P (the protocol WSL "
            "uses to serve Windows drives), so this score mostly measures "
            "that crossing. On one measured PC, creating 2,000 small files "
            "was orders of magnitude slower there than on the Linux "
            "filesystem. Move the repo; nothing else matters until this is fixed.",
            ["Probe: the benchmark work directory for this run is under /mnt/ "
             "(a Windows drive seen from WSL)."],
            ["Clone repos inside the Linux filesystem (for example ~/code).",
             "Access them from Windows via \\\\wsl$ only when you must."],
            file_ids + [b for b in rr if b.startswith(("git.", "search.", "transfer."))],
            result, reference, priority=100))
    elif _cwd_on_windows_drive(s):
        out.append(_cause(
            "Launched from a Windows drive path (/mnt/...)", "observed",
            "You started this run from a directory under /mnt/, but the "
            "benchmark work directory is on the Linux filesystem, so this "
            "score is not affected. Repos you keep under /mnt/ still pay the "
            "full 9P cost for every file operation, which was orders of "
            "magnitude slower for small files on one measured PC.",
            ["Probe: the launch directory is under /mnt/; the work directory is not."],
            ["Keep repos inside the Linux filesystem (for example ~/code), "
             "and run agents from there."],
            [], result, reference, priority=-10,
            affected_text="none in this run; repos kept under /mnt/ pay per file operation"))
    if s.get("workdir_in_icloud"):
        out.append(_cause(
            "Work directory in a synced folder (iCloud/CloudStorage)", "observed",
            "Sync daemons watch and upload every file change. Agent workloads "
            "create thousands of small files, so the sync layer becomes the disk.",
            ["Probe: the work directory is inside a cloud-synced folder."],
            ["Move repos outside synced folders (for example ~/code).",
             "The same applies to OneDrive and Dropbox folders on Windows."],
            file_ids, result, reference))
    fs = str(s.get("fs_type") or "").lower()
    if fs in ("tmpfs", "ramfs", "overlay"):
        out.append(_cause(
            "Work directory measures RAM, not storage", "observed",
            "The work directory is on {}. Disk numbers in this run are not "
            "comparable with real storage.".format(fs),
            ["Probe: fs_type is {}.".format(fs)],
            ["Rerun with --workdir on the disk where your repos live."],
            [], result, reference,
            affected_text="the disk tests in this run (files, transfer, and git on disk)"))
    if s.get("rotational"):
        out.append(_cause(
            "Spinning disk (HDD)", "observed",
            "The work directory sits on a rotational disk. Cold reads, durable "
            "writes, and many-small-file work are seek-bound.",
            ["Probe: the block device reports rotational=1."],
            ["Put repos and caches on an SSD/NVMe volume.",
             "If the HDD is a NAS/RAID volume, keep only archives there."],
            [b for b in rr if b.startswith("transfer.") or b in file_ids], result, reference))
    if s.get("raid_resync"):
        out.append(_cause(
            "RAID resync/check in progress", "observed",
            "A RAID resync competes for the same disks. Disk results from this "
            "run are lower than the machine's normal speed.",
            ["Probe: /proc/mdstat reports an active resync/check."],
            ["Wait for the resync to finish and rerun before judging the machine."],
            [], result, reference,
            affected_text="the disk tests in this run (files, transfer, and git on disk)"))

    # Indexing.
    top = s.get("top_processes") or []
    indexer = next((p for p in top if str(p.get("name", "")).lower() in
                    ("searchindexer", "mds", "mdworker", "searchprotocolhost")), None)
    spotlight_on = "Indexing enabled" in str(s.get("spotlight", ""))
    if indexer and float(indexer.get("cpu") or 0) >= 20:
        out.append(_cause(
            "OS search indexing during the run", "likely",
            "The search indexer used {:.0f}% CPU while the suite ran. Indexers "
            "open every new file, exactly what the small-file tests create.".format(float(indexer["cpu"])),
            ["Probe: {} at {:.0f}% CPU in the top-process snapshot.".format(indexer["name"], float(indexer["cpu"]))],
            ["Exclude code folders and ~/.agentperf from indexing (macOS: System Settings > Spotlight > Search Privacy; Windows: Settings > Privacy & security > Searching Windows)."],
            file_ids, result, reference))
    elif spotlight_on and slow(file_ids):
        out.append(_cause(
            "Spotlight indexes the work volume", "possible",
            "Spotlight indexing is enabled and small-file tests trail. Indexers "
            "add work to every file creation.",
            ["Probe: mdutil reports indexing enabled on the work volume."],
            ["Add code folders and ~/.agentperf to Spotlight Search Privacy, then rerun to confirm the gain."],
            file_ids, result, reference))

    # Background load / power / thermal (trust warnings with real cost).
    load = s.get("load_avg_start")
    cpus = s.get("logical_cpus") or 1
    heavy = [p for p in top if float(p.get("cpu") or 0) >= 80]
    anon_heavy = int(s.get("processes_over_80pct") or 0)
    if (load and load[0] > 0.5 * cpus) or heavy or anon_heavy:
        ev = []
        if load:
            ev.append("Probe: load average {:.1f} on {} logical CPUs at start.".format(load[0], cpus))
        if heavy:
            ev.append("Probe: busy processes: {}.".format(
                ", ".join("{} {:.0f}%".format(p["name"], float(p["cpu"])) for p in heavy)))
        elif anon_heavy:
            ev.append("Probe: {} background process(es) over 80% CPU during the run "
                      "(names withheld in this anonymized profile).".format(anon_heavy))
        ev.append("Context: a stuck background daemon held a measured laptop at 296-430, "
                  "against about 720 modelled for that hardware idle (a model, not a measurement).")
        out.append(_cause(
            "Background load during the run", "observed" if (heavy or anon_heavy) else "likely",
            "Other work competed with the benchmarks. Treat this score as a "
            "lower bound for the hardware, and as the truth for agents that "
            "share the machine with that load.",
            ev,
            ["Quit or restart the busy processes (a daemon pinned near 100% for hours is a bug, not load).",
             "Rerun with --runs 3 on an idle, plugged-in machine for a hardware number."],
            [], result, reference, affected_text="every test in this run"))
    if s.get("power_source") == "battery" or s.get("low_power_mode") or s.get("thermal_warning"):
        ev = []
        if s.get("power_source") == "battery":
            ev.append("Probe: the Mac ran on battery.")
        if s.get("low_power_mode"):
            ev.append("Probe: Low Power Mode is on.")
        if s.get("thermal_warning"):
            ev.append("Probe: macOS recorded a thermal warning.")
        out.append(_cause(
            "Power or thermal limit", "observed",
            "The machine was power- or thermally limited during the run, which "
            "caps burst CPU speed - exactly what short agent commands use.",
            ev,
            ["Plug in power, turn off Low Power Mode, and rerun on a cool machine."],
            [b for b in rr if b.startswith("compute.")], result, reference))
    plan = str(s.get("power_plan") or "").lower()
    if plan and ("saver" in plan or "balanced" in plan):
        out.append(_cause(
            "Windows power plan", "possible",
            "The active plan is '{}'. Balanced and saver plans can add latency "
            "to short bursts and I/O. This is a pattern, not a measurement: "
            "agentperf has not measured the gain from changing the plan, and "
            "the WSL2 reference ran on this same kind of plan on its host PC.".format(s.get("power_plan")),
            ["Probe: powercfg reports '{}'.".format(s.get("power_plan")),
             "Note: the reference machine's host PC used a balanced plan too, "
             "so the plan alone cannot explain a gap to the reference."],
            ["If short bursts feel laggy, try High performance and rerun to measure the difference."],
            process_ids, result, reference, estimate_gain=False))
    gov = s.get("governor")
    if gov and gov not in ("performance", "schedutil"):
        out.append(_cause(
            "CPU governor", "observed",
            "The Linux CPU governor is '{}', which ramps clocks slowly for "
            "short bursts.".format(gov),
            ["Probe: scaling_governor is {}.".format(gov)],
            ["Use performance or schedutil on machines that host agents."],
            [b for b in rr if b.startswith("compute.")], result, reference))

    # Shell startup (diag tests are not scored, but agents pay this per call).
    login = _median(result, "diag.login_shell")
    shell = _median(result, "process.shell")
    if login and login > 100:
        out.append(_cause(
            "Slow login shell (dotfiles)", "observed",
            "Your login shell takes {:.0f} ms to start, timed the way Codex "
            "runs it (zsh -lc: login files such as .zprofile, not .zshrc). "
            "Codex pays this on every command; at {:.0f} ms it can exceed "
            "the command's own work.".format(login, login),
            ["Measurement: diag.login_shell median {:.0f} ms vs {:.1f} ms for a bare shell.".format(
                login, shell or 0)],
            ["Profile the slow lines in your login files (.zprofile; zsh: zmodload zsh/zprof) and lazy-load version managers.",
             "Keep heavy initializers out of the login path used by non-interactive shells."],
            [], result, reference,
            affected_text="not scored; paid per tool call that starts a login shell"))
    ps = _median(result, "diag.powershell")
    if ps and shell and ps > 3 * shell:
        out.append(_cause(
            "PowerShell start cost on Windows", "observed",
            "PowerShell starts in {:.0f} ms vs {:.0f} ms for Git Bash. Harnesses "
            "that run commands through PowerShell pay this per command.".format(ps, shell),
            ["Measurement: diag.powershell {:.0f} ms; process.shell {:.0f} ms.".format(ps, shell)],
            ["Prefer WSL2 or Git Bash execution for agents where the harness allows it.",
             "Trim the PowerShell profile; the no-profile number is the floor."],
            [], result, reference,
            affected_text="not scored; paid per tool call that starts PowerShell"))

    # Git fsmonitor opportunity (directly measured, not inferred).
    status = _median(result, "git.status")
    fsmon = _median(result, "diag.git_fsmonitor")
    if status and fsmon and fsmon < 0.7 * status:
        out.append(_cause(
            "Git without fsmonitor", "observed",
            "git status drops from {:.0f} ms to {:.0f} ms with the built-in "
            "fsmonitor on this machine. Agents run status constantly.".format(status, fsmon),
            ["Measurement: git.status {:.0f} ms; diag.git_fsmonitor {:.0f} ms.".format(status, fsmon)],
            ["git config --global core.fsmonitor true",
             "git config --global core.untrackedCache true"],
            ["git.status", "git.status_dirty"], result, reference))

    # Single-thread vs multi-thread shape. Not meaningful when this is the
    # same CPU as the reference (parallel tests then suffer from whatever
    # slows process starts, not from core count).
    st, mt = rr.get("compute.sha256"), rr.get("compute.parallel")
    ref_cpu = str((reference.get("system", {}) or {}).get("cpu_model", ""))
    same_cpu = bool(ref_cpu) and ref_cpu == str(s.get("cpu_model", ""))
    if st and mt and st >= 0.9 and mt < 0.6 * st and not same_cpu:
        out.append(_cause(
            "Few cores or parallel throttling", "likely",
            "One-core compute matches the reference but the all-core test does "
            "not. Parallel builds and test suites feel this; single commands do not.",
            ["Pattern: compute.sha256 ratio {:.2f}; compute.parallel ratio {:.2f}.".format(st, mt),
             "Probe: {} logical CPUs reported.".format(cpus)],
            ["Nothing to tune if the core count is simply lower; run heavy parallel suites in CI or on a bigger host.",
             "If cores exist but do not engage, check VM CPU limits and thermal state."],
            ["compute.parallel"], result, reference))
    if st and st < 0.6 and mt and mt >= st:
        out.append(_cause(
            "Single-thread CPU speed", "likely",
            "One-core work (hashing, compression, JSON, interpreter loops) "
            "trails the reference. Most agent tool calls are short and "
            "single-threaded, so this shows up everywhere.",
            ["Pattern: compute.sha256 ratio {:.2f} vs reference CPU {}.".format(
                st, (reference.get("system", {}) or {}).get("cpu_model", "reference")),
             "This machine: {}.".format(s.get("cpu_model", "unknown CPU"))],
            ["This is mostly hardware. Weight it when choosing a host: agent work rewards fast single cores."],
            [b for b in rr if b.startswith("compute.")], result, reference))

    # Old Python only matters for the interpreter-bound test.
    py_ratio = rr.get("compute.python")
    ref_py = str((reference.get("system", {}) or {}).get("python", ""))
    if py_ratio and py_ratio < 0.6 and s.get("python") and ref_py \
            and version_tuple(s["python"]) < version_tuple(ref_py):
        out.append(_cause(
            "Older Python interpreter", "possible",
            "The pure-Python test trails and this machine runs Python {} vs "
            "{} on the reference. Newer CPython releases are much faster at "
            "interpreter loops, and the Python build also affects the "
            "C-backed compute tests and every process the harness spawns "
            "from Python. The build is recorded in every result.".format(s["python"], ref_py),
            ["Probe: python {} here, {} on the reference (compared as version numbers).".format(
                s["python"], ref_py),
             "Pattern: compute.python ratio {:.2f}; C-backed compute tests are less affected.".format(py_ratio)],
            ["Use a newer Python (the reference runs {}).".format(ref_py)],
            ["compute.python"], result, reference))

    out.sort(key=lambda c: (-(c.get("priority") or 0), -(c["gain_points"] or 0)))
    return out


def placement(result, reference, others) -> Dict[str, object]:
    """Honest placement against a small reference set. No fake percentiles.

    Every score is computed on the scored tests all of these documents
    share, so the rank never mixes different test subsets."""
    docs = [result, reference] + list(others)
    common = scoring.common_benchmarks(docs)
    restricted = scoring.score(result, reference, restrict=common)
    mine = restricted["agentmark"]
    full = scoring.score(result, reference)
    rows = []
    for doc in others:
        try:
            mark = scoring.score(doc, reference, restrict=common)["agentmark"]
        except Exception:
            continue
        if mark:
            rows.append({"label": doc.get("label", "?"), "agentmark": mark})
    rows.sort(key=lambda r: -(r["agentmark"] or 0))
    faster = sum(1 for r in rows if mine and r["agentmark"] and mine > r["agentmark"])
    return {"agentmark": mine, "full_agentmark": full["agentmark"],
            # The interval belongs to the restricted point beside it,
            # never to the full-test score.
            "agentmark_ci": restricted.get("agentmark_ci"),
            "ci_kind": restricted.get("ci_kind"),
            "coverage": full["coverage"],
            "common_tests": len(restricted.get("compared") or []),
            "references": rows, "faster_than": faster,
            "of": len(rows),
            "note": ("Placement uses {} bundled reference profiles only, scored "
                     "on the {} scored tests every profile shares (your full-test "
                     "coverage is {:.0%}). That is too few machines for a "
                     "meaningful percentile, so agentperf reports a rank, "
                     "not a percentile.".format(
                         len(rows), len(restricted.get("compared") or []),
                         full["coverage"]))}


def verdict(agentmark: Optional[float], os_family: Optional[str] = None) -> str:
    if not agentmark:
        return "No score: run with a reference to get AgentMark."
    if agentmark >= 1200:
        return "Excellent host for coding agents - faster than the reference in most local work."
    if agentmark >= 850:
        return "Strong host for coding agents - close to the reference desktop in WSL2."
    if agentmark >= 550:
        return "Good host. Agents work well; the fixes below can close much of the gap."
    if agentmark >= 300:
        return ("About 2-3x slower than the reference at local agent work. "
                "Agents wait on local tools more than they need to; some of "
                "the gap may be the OS and its tools. See the biggest wins.")
    tail = ("fix the top causes or run agents in WSL2 with repos on the Linux filesystem."
            if os_family == "windows"
            else "fix the top causes or run agents on a faster machine.")
    return "Very slow host for agents. Local waits dominate; " + tail
