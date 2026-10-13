"""Find the external tools that agents call, and fetch a pinned ripgrep.

Every machine should search with the same ripgrep build, so `--fetch-tools`
downloads one pinned release (checked by SHA-256) into ~/.agentperf/tools.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from typing import Dict, List, Optional

from .util import IS_MAC, IS_WINDOWS, IS_WSL, run_text, which

RG_VERSION = "15.2.0"
RG_ASSETS = {
    # (platform key) -> (asset name, sha256)
    "darwin-arm64": ("ripgrep-15.2.0-aarch64-apple-darwin.tar.gz",
                     "3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4"),
    "darwin-x86_64": ("ripgrep-15.2.0-x86_64-apple-darwin.tar.gz",
                      "af7825fcc69a2afc7a7aea55fc9af90e26421d8f20fe59df32e233c0b8a231c1"),
    "linux-x86_64": ("ripgrep-15.2.0-x86_64-unknown-linux-musl.tar.gz",
                     "33e15bcf1624b25cdd2a55813a47a2f95dbe126268203e76aa6a585d1e7b149c"),
    "linux-arm64": ("ripgrep-15.2.0-aarch64-unknown-linux-gnu.tar.gz",
                    "a740b91c82eaf9914cfedd353572f2791cbe0162c84101ee0951058f4dcbc90d"),
    "windows-x86_64": ("ripgrep-15.2.0-x86_64-pc-windows-msvc.zip",
                       "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5"),
}
RG_URL = "https://github.com/BurntSushi/ripgrep/releases/download/{}/{}"

# Node is fetched only where no system Node exists (for example a bare WSL distro).
NODE_VERSION = "22.23.3"
NODE_ASSETS = {
    "darwin-arm64": ("node-v22.23.3-darwin-arm64.tar.gz",
                     "23b25245dcfb9af7262f8ff142e9e2e0af025368117329e7a7458a51e5922f53"),
    "darwin-x86_64": ("node-v22.23.3-darwin-x64.tar.gz",
                      "8a677b0219178efd6eb0e475457c4afb452b521a92f6e67845a73bd85727f2a8"),
    "linux-x86_64": ("node-v22.23.3-linux-x64.tar.gz",
                     "1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af"),
    "linux-arm64": ("node-v22.23.3-linux-arm64.tar.gz",
                    "5ced2d48d1d7198739b7f86804de0171aefb6823b684b12341d3321afc3cb0b2"),
    "windows-x86_64": ("node-v22.23.3-win-x64.zip",
                       "2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71"),
}
NODE_URL = "https://nodejs.org/dist/v{}/{}"

# Well-known paths for tools that are often installed outside PATH.
EXTRA_NODE_PATHS = [
    "/var/packages/Node.js_v22/target/usr/local/bin/node",  # Synology DSM package
    "/var/packages/Node.js_v20/target/usr/local/bin/node",
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
]


def tools_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".agentperf", "tools")


def _platform_key() -> Optional[str]:
    machine = platform.machine().lower()
    arch = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(machine)
    if arch is None:
        return None
    if IS_MAC:
        return "darwin-" + arch
    if IS_WINDOWS:
        return "windows-" + arch
    if sys.platform.startswith("linux"):
        return "linux-" + arch
    return None


def pinned_rg_path() -> str:
    exe = "rg.exe" if IS_WINDOWS else "rg"
    return os.path.join(tools_dir(), "ripgrep-" + RG_VERSION, exe)


def pinned_node_path() -> str:
    exe = "node.exe" if IS_WINDOWS else "node"
    return os.path.join(tools_dir(), "node-v" + NODE_VERSION, exe)


def _fetch_binary(target: str, assets: Dict[str, tuple], url_fmt: str, version: str,
                  log=print) -> Optional[str]:
    """Download one release archive, verify SHA-256, and extract a single executable."""
    if os.path.exists(target):
        return target
    key = _platform_key()
    if key not in assets:
        log("  no pinned build of {} for this platform ({})".format(os.path.basename(target), key))
        return None
    asset, digest = assets[key]
    url = url_fmt.format(version, asset)
    log("  fetching {}".format(url))
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except Exception as exc:  # network errors vary by platform
        log("  download failed: {}".format(exc))
        return None
    if hashlib.sha256(data).hexdigest() != digest:
        log("  checksum mismatch for {}; refusing to use it".format(asset))
        return None
    os.makedirs(os.path.dirname(target), exist_ok=True)
    exe_name = os.path.basename(target)
    wanted = ("/" + exe_name, "/bin/" + exe_name)
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.endswith(wanted)]
            if not names:
                log("  {} does not contain {}; refusing to use it".format(asset, exe_name))
                return None
            member = min(names, key=len)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            members = [m for m in tf.getmembers() if m.name.endswith(wanted) and m.isfile()]
            if not members:
                log("  {} does not contain {}; refusing to use it".format(asset, exe_name))
                return None
            member = min(members, key=lambda m: len(m.name))
            src = tf.extractfile(member)
            with open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def fetch_tools(log=print) -> None:
    """Fetch the pinned ripgrep always, and Node only when no Node is installed."""
    _fetch_binary(pinned_rg_path(), RG_ASSETS, RG_URL, RG_VERSION, log)
    if not _system_node():
        _fetch_binary(pinned_node_path(), NODE_ASSETS, NODE_URL, NODE_VERSION, log)


def _system_node() -> Optional[str]:
    return which("node") or next((p for p in EXTRA_NODE_PATHS if os.path.exists(p)), None)


def _git_for_windows_root(git: Optional[str]) -> Optional[str]:
    """C:\\Program Files\\Git from ...\\Git\\cmd\\git.exe."""
    if not git:
        return None
    root = os.path.dirname(os.path.dirname(git))
    return root if os.path.isdir(os.path.join(root, "usr", "bin")) else None


def find_tools() -> Dict[str, Optional[str]]:
    """Map logical tool names to executable paths (None when missing)."""
    found: Dict[str, Optional[str]] = {}
    found["git"] = which("git")
    pinned = pinned_rg_path()
    found["rg"] = pinned if os.path.exists(pinned) else which("rg")
    pinned_node = pinned_node_path()
    found["node"] = _system_node() or (pinned_node if os.path.exists(pinned_node) else None)
    found["tar"] = which("tar")
    found["claude"] = which("claude")
    found["codex"] = which("codex")
    if IS_WINDOWS:
        git_root = _git_for_windows_root(found["git"])
        bash = os.path.join(git_root, "bin", "bash.exe") if git_root else None
        grep = os.path.join(git_root, "usr", "bin", "grep.exe") if git_root else None
        found["shell"] = bash if bash and os.path.exists(bash) else None  # Git Bash, used by Claude Code
        found["grep"] = grep if grep and os.path.exists(grep) else None
        found["exec"] = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        found["powershell"] = which("powershell")
        found["pwsh"] = which("pwsh")
    else:
        found["shell"] = "/bin/sh" if os.path.exists("/bin/sh") else which("sh")
        found["grep"] = which("grep")
        found["exec"] = which("true")
        found["bash"] = which("bash")
    return found


def tool_versions(found: Dict[str, Optional[str]]) -> Dict[str, str]:
    versions: Dict[str, str] = {}
    probes = {
        "git": ["--version"], "rg": ["--version"], "node": ["--version"], "tar": ["--version"],
        "grep": ["--version"], "claude": ["--version"], "codex": ["--version"],
        "shell": ["--version"] if IS_WINDOWS else None,
    }
    for name, args in probes.items():
        path = found.get(name)
        if not path or args is None:
            continue
        out = run_text([path] + args, timeout=30)
        first = out.splitlines()[0] if out else ""
        versions[name] = first[:120]
    return versions


def describe(found: Dict[str, Optional[str]]) -> List[str]:
    return ["{:<10} {}".format(k, v or "-") for k, v in sorted(found.items())]

