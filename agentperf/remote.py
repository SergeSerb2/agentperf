"""Run agentperf on another machine over SSH.

The package is zipped into one runnable .pyz, copied to ~/.agentperf on the
host, run with the host's own Python, and the result JSON is copied back.
Works with POSIX hosts, Windows hosts whose SSH shell is PowerShell, and WSL
distros reached through a Windows host.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import tempfile
import uuid
import zipfile
from typing import List, Optional

SSH = ["ssh", "-o", "BatchMode=yes"]
SCP = ["scp", "-q", "-o", "BatchMode=yes"]

_LABEL_OK = re.compile(r"^[A-Za-z0-9._-]+$")
_PS_BARE = re.compile(r"^[A-Za-z0-9_.\-/:\\=,]+$")


def sanitize_label(label: str) -> str:
    """Labels land in remote file names and shell commands; keep them tame."""
    if not _LABEL_OK.match(label or ""):
        raise SystemExit("label {!r} may only contain letters, digits, '.', '_' and '-'".format(label))
    return label


def quote_posix(argv: List[str]) -> str:
    return shlex.join(argv)


def quote_powershell(argv: List[str]) -> str:
    """Join argv for a PowerShell command line (single quotes, '' escapes a quote).

    A quoted executable path needs the call operator ``&`` or PowerShell
    treats it as a string literal, so prefix ``&`` when argv[0] is quoted.
    """
    out = []
    for arg in argv:
        out.append(arg if _PS_BARE.match(arg) else "'" + arg.replace("'", "''") + "'")
    joined = " ".join(out)
    if out and out[0].startswith("'"):
        joined = "& " + joined
    return joined


def _running_archive() -> Optional[str]:
    """Path of the zip this module was imported from, or None on a real disk install."""
    path = os.path.abspath(__file__)
    parts = path.split(os.sep)
    for i in range(1, len(parts)):
        candidate = os.sep.join(parts[:i])
        if candidate and os.path.isfile(candidate) and zipfile.is_zipfile(candidate):
            return candidate
    return None


def build_pyz(dest: str) -> str:
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.isdir(pkg_dir):
            for root, dirs, files in os.walk(pkg_dir):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    if name.endswith((".py", ".json")) or name == "DEFAULT":
                        full = os.path.join(root, name)
                        arc = "agentperf/" + os.path.relpath(full, pkg_dir).replace(os.sep, "/")
                        zf.write(full, arc)
        else:  # running from inside a .pyz: copy the package out of the archive
            archive = _running_archive()
            if not archive:
                raise RuntimeError("cannot locate the agentperf package files to copy")
            with zipfile.ZipFile(archive) as src:
                copied = 0
                for name in src.namelist():
                    base = name.rsplit("/", 1)[-1]
                    if name.startswith("agentperf/") and "__pycache__" not in name and (
                            base.endswith((".py", ".json")) or base == "DEFAULT"):
                        zf.writestr(name, src.read(name))
                        copied += 1
            if not copied:
                raise RuntimeError("no agentperf package files found in " + archive)
        zf.writestr("__main__.py", "import sys\nfrom agentperf.cli import main\nsys.exit(main())\n")
    return dest


def _ssh(host: str, command: str, stream: bool = False) -> subprocess.CompletedProcess:
    if stream:
        return subprocess.run(SSH + [host, command])
    return subprocess.run(SSH + [host, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _is_windows(host: str) -> bool:
    proc = _ssh(host, "uname -s")
    if proc.returncode == 255:  # ssh itself failed: unreachable host, auth, DNS
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SystemExit("cannot reach {!r} over SSH{}".format(
            host, ": " + detail[-1] if detail else ""))
    out = proc.stdout.decode("utf-8", "replace").strip()
    return proc.returncode != 0 or out.startswith(("MINGW", "MSYS", "CYGWIN"))


def run_remote(args) -> int:
    host = args.host
    windows = _is_windows(host)
    label = args.label or re.sub(r"[^A-Za-z0-9._-]", "-", host) + ("-wsl" if args.wsl else "")
    sanitize_label(label)
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

    # Unique per invocation: a failed run must never fetch an older result.
    remote_out = ".agentperf/result-{}-{}.json".format(label, uuid.uuid4().hex[:10])
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
        command = quote_powershell(
            ["wsl", "-d", args.wsl, "--", python, "-u", ".agentperf/agentperf.pyz"] + sub)
    elif windows:
        python = args.python or "python"
        command = quote_powershell([python, "-u", ".agentperf\\agentperf.pyz"] + sub)
    else:
        python = args.python or "python3"
        command = "cd ~ && " + quote_posix([python, "-u", ".agentperf/agentperf.pyz"] + sub)
    print("$ ssh {} {}".format(host, command), flush=True)
    code = _ssh(host, command, stream=True).returncode
    if code != 0:
        print("remote run exited with code {}".format(code))

    os.makedirs(args.out_dir, exist_ok=True)
    local = os.path.join(args.out_dir, "{}{}.json".format(label, "" if sub[0] == "run" else "-" + sub[0]))
    fetched = subprocess.run(SCP + ["{}:{}".format(host, remote_out), local]).returncode == 0
    if fetched:
        # The fetched file must be this run's result, not something else.
        try:
            with open(local, encoding="utf-8") as fh:
                doc = json.load(fh)
            if doc.get("label") != label:
                print("fetched result label {!r} does not match {!r}; refusing it".format(
                    doc.get("label"), label))
                fetched = False
        except (OSError, ValueError) as exc:
            print("fetched result is not valid JSON: {}".format(exc))
            fetched = False
    if fetched:
        print("Fetched {}".format(local))
        _ssh(host, ("Remove-Item -Force " if windows else "rm -f ") + remote_out)
    if code != 0:
        return code  # the remote failure status wins, fetched file or not
    return 0 if fetched else 1
