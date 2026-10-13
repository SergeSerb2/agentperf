"""Small helpers shared by every module: platform checks, timing, stats, processes."""

from __future__ import annotations

import math
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from typing import Dict, Iterable, List, Optional, Sequence

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

now = time.perf_counter


def is_wsl() -> bool:
    if not IS_LINUX:
        return False
    try:
        with open("/proc/sys/kernel/osrelease", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


IS_WSL = is_wsl()


def os_family() -> str:
    if IS_WSL:
        return "wsl"
    if IS_MAC:
        return "macos"
    if IS_WINDOWS:
        return "windows"
    if IS_LINUX:
        return "linux"
    return platform.system().lower()


def which(name: str) -> Optional[str]:
    """shutil.which, but ignore Windows binaries leaking into WSL through /mnt/<drive>."""
    found = shutil.which(name)
    if not found:
        return None
    if IS_WSL and found.startswith("/mnt/"):
        path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if not p.startswith("/mnt/")]
        return shutil.which(name, path=os.pathsep.join(path))
    return found


def run(argv: Sequence[str], cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
        timeout: float = 120.0, capture: bool = True, check: bool = False,
        input_bytes: Optional[bytes] = None) -> subprocess.CompletedProcess:
    """Run a command with no console window on Windows and stdin closed."""
    kwargs = {}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        input=input_bytes,
        stdin=None if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        timeout=timeout,
        check=check,
        **kwargs,
    )


# Shell-startup variables a scored launch must never honour. bash reads
# BASH_ENV for non-interactive shells even with --noprofile --norc, and
# sh/ksh read ENV, so an inherited value would time the user's startup
# scripts instead of the shell start. SHELLOPTS/BASHOPTS can also be
# imported from the environment. They are removed, never replaced.
SHELL_ENV_REMOVED = ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS")
NODE_ENV_REMOVED = ("NODE_OPTIONS",)


def shell_env(base: Optional[Dict[str, str]] = None, node: bool = False) -> Dict[str, str]:
    """A copy of *base* (default: this process's environment) with the
    shell-startup overrides removed. Scored shell launches and the Node
    spawn diagnostic run with this environment; everything else (PATH,
    locale, temp dirs) is inherited unchanged, so a machine where none of
    these variables is set does exactly the same work as before."""
    env = dict(os.environ if base is None else base)
    for key in SHELL_ENV_REMOVED + (NODE_ENV_REMOVED if node else ()):
        env.pop(key, None)
    return env


def run_text(argv: Sequence[str], timeout: float = 20.0, **kw) -> str:
    """Best-effort command output as text. Returns '' on any failure."""
    try:
        proc = run(argv, timeout=timeout, **kw)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or b"").decode("utf-8", "replace").strip()


def rmtree(path: str) -> None:
    """Delete a tree, clearing read-only bits that git leaves on Windows."""
    if not os.path.lexists(path):
        return

    def retry(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


# ---------------------------------------------------------------- statistics

def median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return float("nan")
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def percentile(values: Sequence[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return float("nan")
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def cv(values: Sequence[float]) -> float:
    """Coefficient of variation (stdev / mean)."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var) / abs(mean)


def summarize(samples: Sequence[float]) -> Dict[str, float]:
    return {
        "median": median(samples),
        "p10": percentile(samples, 0.10),
        "p90": percentile(samples, 0.90),
        "min": min(samples),
        "max": max(samples),
        "cv": cv(samples),
        "n": len(samples),
    }


def geomean(values: Iterable[float], weights: Optional[Iterable[float]] = None) -> float:
    vals = list(values)
    if not vals:
        return float("nan")
    ws = list(weights) if weights is not None else [1.0] * len(vals)
    total = sum(ws)
    return math.exp(sum(w * math.log(v) for v, w in zip(vals, ws)) / total)


def bootstrap_medians(samples: Sequence[float], rounds: int, rng: random.Random) -> List[float]:
    n = len(samples)
    return [median([samples[rng.randrange(n)] for _ in range(n)]) for _ in range(rounds)]


# ---------------------------------------------------------------- formatting

def fmt_num(value: Optional[float], digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if value == 0:
        return "0"
    mag = abs(value)
    if mag >= 1000:
        return "{:,.0f}".format(value)
    decimals = max(0, digits - 1 - int(math.floor(math.log10(mag))))
    return "{:.{}f}".format(value, min(decimals, 4))


def fmt_gain(value: Optional[float]) -> str:
    """AgentMark gains are counterfactual estimates; whole numbers only."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return "{:,.0f}".format(round(float(value)))


def fmt_ratio(value: Optional[float]) -> str:
    """Speed ratio for display; never prints a misleading x0.00."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    if 0 < value < 0.01:
        return "<0.01"
    return "{:.2f}".format(value)


def version_tuple(text) -> tuple:
    """Numeric version tuple from '3.11.9' style text; () when unparsable."""
    out = []
    for part in str(text or "").split("."):
        digits = ""
        for ch in part.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def buffered_copy(src: str, dst: str, bufsize: int = 4 * 1024 * 1024) -> int:
    """Copy by explicit read/write, so bytes really move on every platform.

    ``shutil`` fast paths can turn a copy into a metadata clone (reflink via
    ``copy_file_range`` on some filesystems). Scored copies must not do that.
    Returns the number of bytes copied.
    """
    total = 0
    sfd = os.open(src, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        dfd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o644)
        try:
            while True:
                data = os.read(sfd, bufsize)
                if not data:
                    break
                total += len(data)
                view = memoryview(data)
                while view:
                    written = os.write(dfd, view)
                    view = view[written:]
        finally:
            os.close(dfd)
    finally:
        os.close(sfd)
    return total


def copy_tree_buffered(src: str, dst: str):
    """Copy a directory tree with buffered_copy for every file.

    Returns (file count, byte count) actually copied, so callers can
    verify the copy outside timing. A missing or unreadable source is an
    error: ``os.walk`` would otherwise skip it silently and the caller
    would credit a copy that never happened.
    """
    if not os.path.isdir(src):
        raise FileNotFoundError("copy source is not a directory: " + src)

    def _raise(exc):
        raise exc

    os.makedirs(dst)
    files_done = 0
    bytes_done = 0
    for root, dirs, files in os.walk(src, onerror=_raise):
        rel = os.path.relpath(root, src)
        target_root = dst if rel == "." else os.path.join(dst, rel)
        for name in dirs:
            os.makedirs(os.path.join(target_root, name), exist_ok=True)
        for name in files:
            bytes_done += buffered_copy(os.path.join(root, name),
                                        os.path.join(target_root, name))
            files_done += 1
    return files_done, bytes_done


def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return "{:.1f} {}".format(n, unit) if unit != "B" else "{} B".format(int(n))
        n /= 1024.0
    return str(n)
