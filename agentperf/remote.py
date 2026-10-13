"""Run agentperf on another machine over SSH.

The package is zipped into one runnable .pyz, copied to ~/.agentperf on the
host, run with the host's own Python, and the result JSON is copied back.
Works with POSIX hosts, Windows hosts whose SSH shell is PowerShell, and WSL
distros reached through a Windows host.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import zipfile
from typing import List

SSH = ["ssh", "-o", "BatchMode=yes"]
SCP = ["scp", "-q", "-o", "BatchMode=yes"]


def build_pyz(dest: str) -> str:
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(pkg_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in files:
                if name.endswith((".py", ".json")) or name == "DEFAULT":
                    full = os.path.join(root, name)
                    arc = "agentperf/" + os.path.relpath(full, pkg_dir).replace(os.sep, "/")
                    zf.write(full, arc)
        zf.writestr("__main__.py", "import sys\nfrom agentperf.cli import main\nsys.exit(main())\n")
    return dest


def _ssh(host: str, command: str, stream: bool = False) -> subprocess.CompletedProcess:
    if stream:
        return subprocess.run(SSH + [host, command])
    return subprocess.run(SSH + [host, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _is_windows(host: str) -> bool:
    proc = _ssh(host, "uname -s")
    out = proc.stdout.decode("utf-8", "replace").strip()
    return proc.returncode != 0 or out.startswith(("MINGW", "MSYS", "CYGWIN"))


def run_remote(args) -> int:
    host = args.host
    windows = _is_windows(host)
    label = args.label or (host + ("-wsl" if args.wsl else ""))
    if args.wsl and not windows:
        raise SystemExit("--wsl needs a Windows host")
    print("Remote {} ({}{})".format(host, "windows" if windows else "posix",
                                    ", WSL " + args.wsl if args.wsl else ""), flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        pyz = build_pyz(os.path.join(tmp, "agentperf.pyz"))
        mkdir = "New-Item -ItemType Directory -Force .agentperf | Out-Null" if windows else "mkdir -p .agentperf"
        if _ssh(host, mkdir).returncode != 0:
            raise SystemExit("could not create ~/.agentperf on " + host)
        if subprocess.run(SCP + [pyz, "{}:.agentperf/agentperf.pyz".format(host)]).returncode != 0:
            raise SystemExit("could not copy agentperf.pyz to " + host)

    remote_out = ".agentperf/result-{}.json".format(label)
    sub: List[str] = [getattr(args, "mode", "run") or "run"]
    if sub[0] == "run":
        sub += ["--label", label, "--profile", args.profile, "--out", remote_out]
        if args.fetch_tools:
            sub.append("--fetch-tools")
        if args.clean:
            sub.append("--clean")
        if args.no_network:
            sub.append("--no-network")
        if args.only:
            sub += ["--only", args.only]
    else:
        sub += ["--label", label, "--out", remote_out]
    sub += list(getattr(args, "extra", None) or [])

    if args.wsl:
        python = args.python or "python3"
        command = "wsl -d {} -- {} -u .agentperf/agentperf.pyz {}".format(args.wsl, python, " ".join(sub))
    elif windows:
        python = args.python or "python"
        command = "{} -u .agentperf\\agentperf.pyz {}".format(python, " ".join(sub))
    else:
        python = args.python or "python3"
        command = "cd ~ && {} -u .agentperf/agentperf.pyz {}".format(python, " ".join(sub))
    print("$ ssh {} {}".format(host, command), flush=True)
    code = _ssh(host, command, stream=True).returncode
    if code != 0:
        print("remote run exited with code {}".format(code))

    os.makedirs(args.out_dir, exist_ok=True)
    local = os.path.join(args.out_dir, "{}{}.json".format(label, "" if sub[0] == "run" else "-" + sub[0]))
    fetched = subprocess.run(SCP + ["{}:{}".format(host, remote_out), local]).returncode == 0
    if fetched:
        print("Fetched {}".format(local))
        _ssh(host, ("Remove-Item -Force " if windows else "rm -f ") + remote_out)
    return 0 if fetched else (code or 1)
