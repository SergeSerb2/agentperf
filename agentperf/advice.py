"""Rules that turn system facts and measurements into tuning advice."""

from __future__ import annotations

from typing import Dict, List, Optional


def _median(result, bench_id: str) -> Optional[float]:
    entry = result["benchmarks"].get(bench_id)
    return float(entry["median"]) if entry else None


def advise(result: Dict[str, object]) -> List[Dict[str, str]]:
    """Return a list of {level, topic, text}. Levels: warn (affects trust), tip, info."""
    s = result.get("system", {})
    out: List[Dict[str, str]] = []

    def add(level, topic, text):
        out.append({"level": level, "topic": topic, "text": text})

    # --- results you should not trust
    if s.get("raid_resync"):
        add("warn", "RAID", "A RAID resync or check is running. Disk results are lower than normal.")
    load = s.get("load_avg_start")
    cpus = s.get("logical_cpus") or 1
    if load and load[0] > 0.5 * cpus:
        add("warn", "Load", "The system was busy at the start (load {:.1f} on {} CPUs). Close heavy apps "
                            "and run again for clean numbers.".format(load[0], cpus))
    if s.get("power_source") == "battery":
        add("warn", "Power", "The Mac ran on battery. Plug in power for stable numbers.")
    if s.get("low_power_mode"):
        add("warn", "Power", "Low Power Mode is on. It slows the CPU; turn it off for agent work.")
    if s.get("thermal_warning"):
        add("warn", "Thermal", "macOS recorded a thermal warning. The CPU may be throttled.")
    fs = (s.get("fs_type") or "").lower()
    if fs in ("tmpfs", "ramfs", "overlay"):
        add("warn", "Work dir", "The work directory is on {}; disk numbers measure RAM, not storage.".format(fs))
    if s.get("workdir_on_windows_drive"):
        add("warn", "WSL", "The work directory is under /mnt/. Keep repos in the Linux file system "
                           "(for example ~/code); /mnt/c is many times slower in WSL2.")
    total, free = s.get("disk_total_gb"), s.get("disk_free_gb")
    if total and free is not None and (free < 10 or free / total < 0.05):
        add("warn", "Disk", "Only {:.0f} GB free. Low free space slows APFS and SSD writes and can stop "
                            "agents mid-task.".format(free))
    heavy = [p for p in (s.get("top_processes") or []) if p.get("cpu", 0) >= 80]
    if heavy:
        add("warn", "Background CPU", "Busy processes at the start: {}. They compete with agent tools; "
                                      "a process stuck near 100% (for example identityservicesd) is worth "
                                      "restarting.".format(", ".join("{} {:.0f}%".format(p["name"], p["cpu"]) for p in heavy)))
    noisy = [b for b, e in result["benchmarks"].items() if e.get("noisy") and e.get("scored")]
    if len(noisy) >= 4:
        add("warn", "Noise", "{} scored tests varied by more than 15% between samples: {}.".format(
            len(noisy), ", ".join(sorted(noisy)[:6])))

    # --- tuning tips
    shell = _median(result, "process.shell")
    if shell and shell > 8:
        add("tip", "Process spawn", "Each shell start costs {:.1f} ms. Agents start one for every Bash "
                                    "call.".format(shell))
    login = _median(result, "diag.login_shell")
    if login and login > 100:
        add("tip", "Shell startup", "Your login shell takes {:.0f} ms to start, timed as Codex runs it "
                                    "(zsh -lc: login files such as .zprofile, not .zshrc). Codex pays this "
                                    "on every command. Trim slow lines in your login files.".format(login))
    status = _median(result, "git.status")
    fsmon = _median(result, "diag.git_fsmonitor")
    if status and fsmon and fsmon < 0.7 * status:
        add("tip", "Git", "git status drops from {:.0f} ms to {:.0f} ms with the built-in fsmonitor. Run: "
                          "git config --global core.fsmonitor true && git config --global "
                          "core.untrackedCache true".format(status, fsmon))
    spotlight = str(s.get("spotlight", ""))
    if "Indexing enabled" in spotlight:
        add("tip", "Spotlight", "Spotlight indexes the work volume. Add your code folders (and "
                                "~/.agentperf) to System Settings > Spotlight > Search Privacy.")
    if s.get("time_machine_running"):
        add("tip", "Time Machine", "A Time Machine backup ran during the test. Results may be low.")
    if s.get("workdir_in_icloud"):
        add("tip", "iCloud", "The work directory is in an iCloud-synced folder. Keep repos outside it.")
    agents = s.get("security_agents") or []
    if agents:
        add("info", "Security software", "Found {}. Security scanners slow process starts and file "
                                         "writes; ask IT about a developer-folder exclusion.".format(", ".join(agents)))
    active_av = [a["name"] for a in (s.get("antivirus") or []) if a.get("active")]
    if active_av:
        add("tip", "Antivirus", "Real-time scanning by {} is active. It inspects every process start and "
                                "file write; exclude your code folders, package caches, and ~/.agentperf, "
                                "or move code to a Dev Drive.".format(", ".join(active_av)))
    if s.get("defender_realtime") is True and not s.get("dev_drive"):
        add("tip", "Defender", "Defender real-time protection scans the work drive. A Dev Drive (ReFS) "
                               "puts Defender in performance mode for code folders.")
    indexer = next((p for p in (s.get("top_processes") or []) if p.get("name", "").lower() == "searchindexer"), None)
    if indexer and indexer.get("cpu", 0) >= 20:
        add("tip", "Windows Search", "The Windows Search indexer used {:.0f}% CPU during the run. Exclude code "
                                     "folders and ~/.agentperf from indexing (Settings > Privacy & security > "
                                     "Searching Windows).".format(indexer["cpu"]))
    ps = _median(result, "diag.powershell")
    if ps and shell and ps > 3 * shell:
        add("info", "PowerShell", "PowerShell starts in {:.0f} ms vs {:.0f} ms for Git Bash. Codex runs "
                                  "commands through PowerShell on Windows, so it pays this per command."
                                  .format(ps, shell))
    plan = str(s.get("power_plan", "")).lower()
    if plan and ("saver" in plan or "balanced" in plan):
        add("info", "Power plan", "The Windows power plan is '{}'. A performance plan can cut burst "
                                  "latency, but agentperf has not measured the gain; the reference "
                                  "host used a balanced plan too. Rerun after changing it to see.".format(s.get("power_plan")))
    gov = s.get("governor")
    if gov and gov not in ("performance", "schedutil"):
        add("tip", "CPU governor", "The CPU governor is '{}'. 'performance' or 'schedutil' responds "
                                   "faster to short bursts.".format(gov))
    if s.get("rotational"):
        add("info", "Storage", "The work directory sits on a spinning disk. Cold reads, durable writes, "
                               "and first-time installs are much slower than on SSD.")
    if s.get("filevault", "").startswith("FileVault is On"):
        add("info", "FileVault", "FileVault is on. Its cost on Apple silicon is small; keep it on.")
    return out
