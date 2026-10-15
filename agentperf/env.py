"""System facts and tuning probes. Nothing here needs sudo."""

from __future__ import annotations

import json
import getpass
import os
import platform
import re
import shutil
import socket
import sys
from typing import Dict, List, Optional

from .util import IS_LINUX, IS_MAC, IS_WINDOWS, IS_WSL, os_family, run_text

# Endpoint security agents, matched against lower-case process basenames.
SECURITY_AGENTS = [
    (r"crowdstrike|^falcond$", "CrowdStrike Falcon"),
    (r"sentinel(one|d|agent)", "SentinelOne"),
    (r"jamfprotect|^jamf protect", "Jamf Protect"),
    (r"^wdav|^mdatp|^msmpeng(\.exe)?$", "Microsoft Defender"),
    (r"sophos", "Sophos"),
    (r"carbonblack|^cbagentd$|^cbdefense", "Carbon Black"),
    (r"cylance", "Cylance"),
    (r"elastic-endpoint|elastic-agent", "Elastic Endpoint"),
    (r"^santad$|com\.google\.santa", "Santa"),
    (r"kandji", "Kandji"),
    (r"zscaler", "Zscaler"),
    (r"netskope|^nsauxsvc", "Netskope"),
    (r"^esets?[_ ]|^eset[ .]|esets_daemon|esetservice", "ESET"),
    (r"bitdefender|^bdldaemon|^bdservicehost", "Bitdefender"),
    (r"malwarebytes|^rtprotectiondaemon", "Malwarebytes"),
]


def collect(workdir: str) -> Dict[str, object]:
    info: Dict[str, object] = {
        "hostname": socket.gethostname(),
        "os_family": os_family(),
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_impl": platform.python_implementation(),
        "logical_cpus": os.cpu_count(),
        "workdir": workdir,
    }
    # Who ran it, for `share` only: it finds these names everywhere else
    # in the result (a power plan called "Alice Performance") and then
    # drops both fields, even when the work folder is outside home.
    try:
        info["user"] = getpass.getuser()
    except Exception:  # no login name in this environment
        pass
    info["home"] = os.path.expanduser("~")
    usage = shutil.disk_usage(workdir)
    info["disk_total_gb"] = round(usage.total / 1e9, 1)
    info["disk_free_gb"] = round(usage.free / 1e9, 1)
    if IS_MAC:
        info.update(_mac(workdir))
    elif IS_WINDOWS:
        info.update(_windows(workdir))
    elif IS_LINUX:
        info.update(_linux(workdir))
    if hasattr(os, "getloadavg"):
        info["load_avg_start"] = [round(x, 2) for x in os.getloadavg()]
    for key, probe in (("security_agents", _security_agents), ("top_processes", _top_processes)):
        try:
            info[key] = probe()
        except Exception as exc:  # a probe must never stop a run
            info[key] = []
            info.setdefault("probe_errors", []).append("{}: {}".format(key, exc))
    return info


def _top_processes(limit: int = 5) -> List[Dict[str, object]]:
    """The heaviest CPU users right now (% of one core, like ps)."""
    if IS_WINDOWS:
        data = _powershell_json(
            "(Get-Counter '\\Process(*)\\% Processor Time' -SampleInterval 1).CounterSamples"
            " | Where-Object { $_.InstanceName -notin @('_total','idle','python','powershell') }"
            " | Sort-Object CookedValue -Descending | Select-Object -First " + str(limit) + " InstanceName,CookedValue")
        rows = data if isinstance(data, list) else ([data] if data else [])
        return [{"name": str(r.get("InstanceName"))[:60], "cpu": round(float(r.get("CookedValue") or 0), 1)}
                for r in rows]
    text = run_text(["ps", "-axo", "pcpu=,comm="] if IS_MAC else ["ps", "-eo", "pcpu=,comm="])
    rows = []
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        try:
            cpu = float(parts[0])
        except ValueError:
            continue
        name = os.path.basename(parts[1].strip())
        if name.startswith(("python", "ps")):
            continue  # agentperf itself
        rows.append({"name": name[:60], "cpu": cpu})
    rows.sort(key=lambda r: -r["cpu"])
    return rows[:limit]


# ---------------------------------------------------------------- macOS

def _sysctl(name: str) -> str:
    return run_text(["sysctl", "-n", name])


def _mac(workdir: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    out["os_version"] = run_text(["sw_vers", "-productVersion"])
    out["cpu_model"] = _sysctl("machdep.cpu.brand_string")
    out["model_id"] = _sysctl("hw.model")
    out["physical_cpus"] = _int(_sysctl("hw.physicalcpu"))
    out["perf_cores"] = _int(_sysctl("hw.perflevel0.physicalcpu"))
    out["efficiency_cores"] = _int(_sysctl("hw.perflevel1.physicalcpu"))
    out["ram_gb"] = round(_int(_sysctl("hw.memsize")) / 2 ** 30, 1)
    batt = run_text(["pmset", "-g", "batt"])
    out["power_source"] = "battery" if "Battery Power" in batt else ("ac" if "AC Power" in batt else "unknown")
    pm = run_text(["pmset", "-g"])
    m = re.search(r"lowpowermode\s+(\d)", pm)
    out["low_power_mode"] = bool(m and m.group(1) == "1")
    therm = run_text(["pmset", "-g", "therm"])
    out["thermal_warning"] = "No thermal warning level has been recorded" not in therm and bool(therm)
    df = run_text(["df", "-P", workdir]).splitlines()
    volume = df[-1].split()[-1] if len(df) > 1 else "/"
    out["fs_type"] = _mac_fs_type(workdir)
    out["spotlight"] = run_text(["mdutil", "-s", volume]).replace("\n", " ")[:160]
    out["filevault"] = run_text(["fdesetup", "status"])[:80]
    tm = run_text(["tmutil", "status"])
    out["time_machine_running"] = bool(re.search(r"Running\s*=\s*1", tm))
    out["workdir_in_icloud"] = "Mobile Documents" in workdir or "/Library/CloudStorage/" in workdir
    return out


def _mac_fs_type(path: str) -> str:
    text = run_text(["mount"])
    best, kind = "", ""
    for line in text.splitlines():
        m = re.match(r".* on (.+) \(([^,]+),", line)
        if m and path.startswith(m.group(1)) and len(m.group(1)) > len(best):
            best, kind = m.group(1), m.group(2)
    return kind


# ---------------------------------------------------------------- Linux / WSL

def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _linux(workdir: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    osr = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', _read("/etc/os-release"), re.M))
    out["os_version"] = osr.get("PRETTY_NAME", "")
    cpuinfo = _read("/proc/cpuinfo")
    m = re.search(r"model name\s*:\s*(.+)", cpuinfo)
    out["cpu_model"] = m.group(1).strip() if m else platform.processor()
    cores = set(re.findall(r"physical id\s*:\s*(\d+)[\s\S]*?core id\s*:\s*(\d+)", cpuinfo))
    out["physical_cpus"] = len(cores) or None
    m = re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo"))
    out["ram_gb"] = round(int(m.group(1)) / 2 ** 20, 1) if m else None
    out["wsl"] = IS_WSL
    out["virtualization"] = run_text(["systemd-detect-virt"]) or ("wsl" if IS_WSL else "")
    out["governor"] = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor").strip()
    mount = _linux_mount(workdir)
    out.update(mount)
    virtual = IS_WSL or out["virtualization"] not in ("", "none")
    # Virtual disks often report rotational=1 regardless of the real storage.
    out["rotational"] = None if virtual else _linux_rotational(mount.get("fs_device", ""))
    mdstat = _read("/proc/mdstat")
    out["raid_resync"] = bool(re.search(r"(resync|recovery|reshape|check)\s*=", mdstat))
    out["workdir_on_windows_drive"] = IS_WSL and workdir.startswith("/mnt/")
    try:
        out["cwd_on_windows_drive"] = IS_WSL and os.getcwd().startswith("/mnt/")
    except OSError:
        out["cwd_on_windows_drive"] = False
    return out


def _linux_mount(path: str) -> Dict[str, object]:
    best = ("", "", "", "")
    for line in _read("/proc/self/mountinfo").splitlines():
        left, _, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 6:
            continue
        mnt = fields[4].replace("\\040", " ")
        rest = right.split()
        if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best[0]):
            best = (mnt, rest[0] if rest else "", rest[1] if len(rest) > 1 else "", fields[5])
    return {"fs_mount": best[0], "fs_type": best[1], "fs_device": best[2], "fs_options": best[3]}


def _linux_rotational(device: str) -> Optional[bool]:
    name = os.path.basename(device)
    if not name:
        return None
    for candidate in (name, re.sub(r"p?\d+$", "", name)):
        text = _read("/sys/block/{}/queue/rotational".format(candidate)).strip()
        if text in ("0", "1"):
            return text == "1"
    return None


# ---------------------------------------------------------------- Windows

def _powershell_json(script: str) -> Optional[object]:
    out = run_text(["powershell", "-NoLogo", "-NoProfile", "-Command",
                    script + " | ConvertTo-Json -Compress"], timeout=40)
    try:
        return json.loads(out) if out else None
    except ValueError:
        return None


def _windows(workdir: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    build = _int(platform.version().split(".")[-1])
    out["os_version"] = "Windows {} build {}".format("11" if build >= 22000 else platform.release(), build)
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        out["cpu_model"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        out["cpu_model"] = platform.processor()
    out["ram_gb"] = _windows_ram_gb()
    drive = os.path.splitdrive(os.path.abspath(workdir))[0] + "\\"
    out["fs_type"] = _windows_fs_type(drive)
    defender = _powershell_json("Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled,"
                                "AntivirusEnabled")
    if isinstance(defender, dict):
        out["defender_realtime"] = defender.get("RealTimeProtectionEnabled")
    av = _powershell_json("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntivirusProduct"
                          " | Select-Object displayName,productState")
    if av:
        # productState bits 12-15: 1 = real-time scanning on.
        out["antivirus"] = [{"name": a.get("displayName"), "active": (int(a.get("productState") or 0) >> 12) & 0xF == 1}
                            for a in (av if isinstance(av, list) else [av])]
    plan = run_text(["powercfg", "/getactivescheme"])
    m = re.search(r"\(([^)]+)\)", plan)
    out["power_plan"] = m.group(1) if m else plan[:80]
    search = run_text(["sc", "query", "WSearch"])
    out["windows_search_running"] = "RUNNING" in search
    devdrive = run_text(["fsutil", "devdrv", "query", drive.rstrip("\\")])
    out["dev_drive"] = "trusted developer volume" in devdrive.lower() or "is a dev drive" in devdrive.lower()
    disk = _powershell_json("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,BusType")
    if disk:
        out["physical_disks"] = disk if isinstance(disk, list) else [disk]
    return out


def _windows_ram_gb() -> Optional[float]:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return round(stat.ullTotalPhys / 2 ** 30, 1)
    return None


def _windows_fs_type(drive: str) -> str:
    import ctypes
    name = ctypes.create_unicode_buffer(64)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(ctypes.c_wchar_p(drive), None, 0, None, None,
                                                      None, name, 64)
    return name.value if ok else ""


# ---------------------------------------------------------------- shared

def _security_agents() -> List[str]:
    if IS_WINDOWS:
        rows = run_text(["tasklist", "/fo", "csv", "/nh"], timeout=30).splitlines()
        names = [r.split(",")[0].strip('"').lower() for r in rows if r]
    else:
        text = run_text(["ps", "-axo", "comm"] if IS_MAC else ["ps", "-eo", "comm"])
        names = [os.path.basename(line.strip()).lower() for line in text.splitlines()[1:]]
    found = set()
    for pattern, label in SECURITY_AGENTS:
        rx = re.compile(pattern)
        if any(rx.search(n) for n in names):
            found.add(label)
    return sorted(found)


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return 0
