"""Run agentperf on other machines over SSH.

The package is zipped into one runnable .pyz, copied to ~/.agentperf on the
host, run with the host's own Python, and the result JSON is copied back.
Works with POSIX hosts, Windows hosts whose SSH shell is PowerShell, and WSL
distros reached through a Windows host.

Several hosts run strictly one after another, in the order given: two runs
at once would measure each other, and native Windows beside its own WSL2
would share one PC. The returned summary records which host each fetched
result came from.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import uuid
import zipfile
from typing import Dict, List, Optional, Tuple

SSH = ["ssh", "-o", "BatchMode=yes"]
SCP = ["scp", "-q", "-o", "BatchMode=yes"]

_LABEL_OK = re.compile(r"[A-Za-z0-9._-]+")
_PS_BARE = re.compile(r"[A-Za-z0-9_.\-/:\\=,]+")
# Names Windows reserves as devices, with or without an extension.
_WINDOWS_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM{}".format(i) for i in range(1, 10)]
    + ["LPT{}".format(i) for i in range(1, 10)])


def sanitize_label(label: str) -> str:
    """Labels land in remote file names and shell commands; keep them tame
    and portable: full-string character check (a trailing newline must
    not slip past), no leading dot or dash, no trailing dot, no Windows
    reserved device names."""
    problem = None
    if not isinstance(label, str) or not label or len(label) > 100 \
            or not _LABEL_OK.fullmatch(label):
        problem = "may only contain letters, digits, '.', '_' and '-' (max 100)"
    elif label[0] in ".-" or label[-1] == ".":
        problem = "must start with a letter or digit and not end with '.'"
    elif label.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        problem = "is a reserved Windows file name"
    if problem:
        raise SystemExit("label {!r} {}".format(label, problem))
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
        # fullmatch, not match+$: a trailing newline must force quoting,
        # never ride into the command line bare.
        out.append(arg if _PS_BARE.fullmatch(arg) else "'" + arg.replace("'", "''") + "'")
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
                    if name.endswith((".py", ".json", ".md")) or name == "DEFAULT":
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
                            base.endswith((".py", ".json", ".md")) or base == "DEFAULT"):
                        zf.writestr(name, src.read(name))
                        copied += 1
            if not copied:
                raise RuntimeError("no agentperf package files found in " + archive)
        zf.writestr("__main__.py", "import sys\nfrom agentperf.cli import main\nsys.exit(main())\n")
    return dest


def _ssh(host: str, command: str, stream: bool = False,
         out=None) -> subprocess.CompletedProcess:
    if stream:
        # *out* redirects the remote command's stdout (stderr when a JSON
        # summary must own stdout); None inherits this process's stdout.
        return subprocess.run(SSH + [host, command], stdout=out)
    return subprocess.run(SSH + [host, command], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _is_windows(host: str) -> bool:
    proc = _ssh(host, "uname -s")
    if proc.returncode == 255:  # ssh itself failed: unreachable host, auth, DNS
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise SystemExit("cannot reach {!r} over SSH{}".format(
            host, ": " + detail[-1] if detail else ""))
    out = proc.stdout.decode("utf-8", "replace").strip()
    return proc.returncode != 0 or out.startswith(("MINGW", "MSYS", "CYGWIN"))


def _default_label(host: str, wsl: Optional[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]", "-", host.split("@")[-1])
    if wsl:
        base += "-wsl-" + re.sub(r"[^A-Za-z0-9._-]", "-", wsl).lower()
    return base.strip("-") or "remote"


def parse_targets(args) -> List[Tuple[str, Optional[str], str]]:
    """Resolve hosts and --wsl specs into ordered (host, distro, label) runs.

    ``--wsl DISTRO`` (no colon) is the single-host form: that one host runs
    inside the distro. ``--wsl HOST:DISTRO`` adds one extra WSL run on that
    host, so ``remote my-pc --wsl my-pc:Ubuntu`` runs native Windows first,
    then WSL2 on the same PC. Positional hosts run in the order given,
    then the ``--wsl HOST:DISTRO`` runs in their order.
    """
    hosts = list(getattr(args, "hosts", None) or [])
    legacy_host = getattr(args, "host", None)  # pre-multi-host callers
    if not hosts and legacy_host:
        hosts = [legacy_host]
    specs = getattr(args, "wsl", None) or []
    if isinstance(specs, str):
        specs = [specs]
    plain = [s for s in specs if ":" not in s]
    paired: List[Tuple[str, str]] = []
    for spec in specs:
        if ":" in spec:
            host, _, distro = spec.partition(":")
            if not host or not distro:
                raise ValueError("--wsl {!r}: expected HOST:DISTRO".format(spec))
            paired.append((host, distro))
    if plain and (len(hosts) != 1 or paired or len(plain) > 1):
        raise ValueError("--wsl DISTRO needs exactly one host; with several hosts "
                         "use --wsl HOST:DISTRO for each WSL run")
    if plain:
        targets = [(hosts[0], plain[0])]
    else:
        targets = [(h, None) for h in hosts] + paired
    if not targets:
        raise ValueError("remote needs at least one host")
    explicit = getattr(args, "label", None)
    if explicit and len(targets) > 1:
        raise ValueError("--label names one run; with several hosts the labels come "
                         "from the host names (run hosts separately to set labels)")
    for host, distro in targets:
        # A host that starts with '-' would be read as an ssh/scp option
        # (up to local command execution via proxy options), and
        # whitespace or control characters break command construction.
        if not host or host.startswith("-") or \
                any(c.isspace() or ord(c) < 32 for c in host):
            raise ValueError("invalid SSH host {!r}".format(host))
        if distro is not None and (not distro or distro.startswith("-") or
                                   any(ord(c) < 32 for c in distro)):
            raise ValueError("invalid WSL distro name {!r}".format(distro))
    labelled: List[Tuple[str, Optional[str], str]] = []
    seen: Dict[str, Tuple[str, Optional[str]]] = {}
    for host, distro in targets:
        label = explicit or _default_label(host, distro)
        try:
            sanitize_label(label)
        except SystemExit as exc:  # a bad --label is bad input (exit 2), not a run failure
            raise ValueError(str(exc.code))
        # File names derived from labels collide case-insensitively on
        # Windows (and default macOS) volumes: check before any host runs.
        key = label.casefold()
        if key in seen:
            raise ValueError(
                "hosts {!r} and {!r} would both write result label {!r} "
                "(labels that differ only in case still name the same file "
                "on Windows); run them in separate remote commands with "
                "distinct --label values, or rename one SSH alias".format(
                    seen[key], (host, distro), label))
        seen[key] = (host, distro)
        labelled.append((host, distro, label))
    return labelled


def _subcommand(args, label: str, remote_out: str) -> List[str]:
    mode = getattr(args, "mode", "run") or "run"
    sub: List[str] = [mode]
    if mode == "run":
        profile = "quick" if getattr(args, "quick", False) else args.profile
        sub += ["--label", label, "--profile", profile, "--out", remote_out]
        if getattr(args, "runs", 1) and args.runs > 1:
            sub += ["--runs", str(args.runs)]
        if getattr(args, "workdir", None):
            sub += ["--workdir", args.workdir]
        if args.fetch_tools:
            sub.append("--fetch-tools")
        if args.clean:
            sub.append("--clean")
        if args.no_network:
            sub.append("--no-network")
        if args.only:
            sub += ["--only", args.only]
    elif mode == "doctor":
        # Capture the machine-readable summary: doctor exits 0 even for
        # an unhealthy machine, so health must travel in the JSON.
        sub += ["--json=" + remote_out]
        if getattr(args, "workdir", None):
            sub += ["--workdir", args.workdir]
    else:  # live
        sub += ["--label", label, "--out", remote_out]
    sub += list(getattr(args, "extra", None) or [])
    return sub


def _valid_fetched(doc, label: str, mode: str = "run") -> bool:
    """A fetched file is this run's result only when it is structurally
    a result of its mode, with the fields needed to interpret it: an
    object with the expected label and, for a suite run, a non-empty
    benchmarks object that passes the full result validation
    (``scoring.validate_result``: medians alone do not make a result);
    for live, a non-empty runs list whose entries carry a harness, a
    task, a boolean success, and a wall time; for doctor, the summary
    shape (``ok`` plus a checks list, no label check: doctor summaries
    carry no run label)."""
    if not isinstance(doc, dict):
        return False
    if mode == "doctor":
        return isinstance(doc.get("ok"), bool) \
            and isinstance(doc.get("checks"), list) \
            and all(isinstance(c, str) for c in doc["checks"])
    if doc.get("label") != label:
        return False
    if mode == "live":
        if doc.get("kind") not in (None, "live"):
            return False
        runs = doc.get("runs")
        if not isinstance(runs, list) or not runs:
            return False
        for run in runs:
            if not isinstance(run, dict):
                return False
            if not isinstance(run.get("harness"), str) \
                    or not run.get("harness"):
                return False
            if not isinstance(run.get("task"), str) or not run.get("task"):
                return False
            if not isinstance(run.get("success"), bool):
                return False
            wall = run.get("wall_s")
            if isinstance(wall, bool) \
                    or not isinstance(wall, (int, float)) \
                    or not math.isfinite(float(wall)):
                return False
            if "exit_code" in run and run["exit_code"] is not None \
                    and (isinstance(run["exit_code"], bool)
                         or not isinstance(run["exit_code"], int)):
                return False
        return True
    benchmarks = doc.get("benchmarks")
    if not isinstance(benchmarks, dict) or not benchmarks:
        return False
    from . import scoring
    try:
        scoring.validate_result(doc, "fetched result")
    except ValueError:
        return False
    return True


def _reject_constant(name: str):
    raise ValueError("non-finite number {!r} in fetched JSON".format(name))


def _run_one(host: str, wsl: Optional[str], label: str, args, pyz: str,
             say) -> Dict[str, object]:
    """Copy, run, and fetch for one target. Raises SystemExit on setup failure."""
    windows = _is_windows(host)
    if wsl and not windows:
        raise SystemExit("--wsl needs a Windows host ({} is not Windows)".format(host))
    mode = getattr(args, "mode", "run") or "run"
    record: Dict[str, object] = {"host": host, "wsl": wsl, "label": label,
                                 "mode": mode, "exit_code": None,
                                 "result_file": None, "error": None}
    say("Remote {} ({}{})".format(host, "windows" if windows else "posix",
                                  ", WSL " + wsl if wsl else ""))
    mkdir = ("New-Item -ItemType Directory -Force .agentperf | Out-Null" if windows
             else "mkdir -p .agentperf")
    if _ssh(host, mkdir).returncode != 0:
        raise SystemExit("could not create ~/.agentperf on " + host)
    if subprocess.run(SCP + [pyz, "{}:.agentperf/agentperf.pyz".format(host)]).returncode != 0:
        raise SystemExit("could not copy agentperf.pyz to " + host)

    # Unique per invocation: a failed run must never fetch an older result.
    remote_out = ".agentperf/{}-{}-{}.json".format(
        "doctor" if mode == "doctor" else "result",
        label, uuid.uuid4().hex[:10])
    sub = _subcommand(args, label, remote_out)
    if wsl:
        python = args.python or "python3"
        command = quote_powershell(
            ["wsl", "-d", wsl, "--", python, "-u", ".agentperf/agentperf.pyz"] + sub)
    elif windows:
        python = args.python or "python"
        command = quote_powershell([python, "-u", ".agentperf\\agentperf.pyz"] + sub)
    else:
        python = args.python or "python3"
        command = "cd ~ && " + quote_posix([python, "-u", ".agentperf/agentperf.pyz"] + sub)
    say("$ ssh {} {}".format(host, command))
    json_stdout = getattr(args, "json", None) == "-"
    code = _ssh(host, command, stream=True, out=sys.stderr if json_stdout else None).returncode
    record["exit_code"] = code
    if code != 0:
        say("remote {} on {} exited with code {}".format(mode, host, code))
    remove_remote = ("Remove-Item -Force " if windows else "rm -f ") + remote_out
    if mode == "doctor":
        # Fetch the doctor summary itself: the exit code says the check
        # ran, the summary's ok/checks say whether the machine is well.
        record["doctor"] = None
        tmp = tempfile.NamedTemporaryFile(
            prefix="agentperf-doctor-", suffix=".json", delete=False)
        tmp.close()
        try:
            got = subprocess.run(
                SCP + ["{}:{}".format(host, remote_out), tmp.name]).returncode == 0
            doc = None
            if got:
                try:
                    with open(tmp.name, encoding="utf-8") as fh:
                        doc = json.load(fh, parse_constant=_reject_constant)
                except (OSError, ValueError):
                    doc = None
            if doc is not None and _valid_fetched(doc, label, "doctor"):
                record["doctor"] = doc
                _ssh(host, remove_remote)
            else:
                record["error"] = "doctor summary could not be fetched from " + host
                say("error: {}: {}".format(host, record["error"]))
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
        return record

    os.makedirs(args.out_dir, exist_ok=True)
    local = os.path.join(args.out_dir, "{}{}.json".format(
        label, "" if mode == "run" else "-" + mode))
    fetched = subprocess.run(SCP + ["{}:{}".format(host, remote_out), local]).returncode == 0
    if fetched:
        # The fetched file must be this run's result, not something else.
        try:
            with open(local, encoding="utf-8") as fh:
                doc = json.load(fh, parse_constant=_reject_constant)
            if not _valid_fetched(doc, label, mode):
                shown = doc.get("label") if isinstance(doc, dict) else type(doc).__name__
                say("fetched result is not a valid {} result for {!r} (got {!r}); "
                    "refusing it".format(mode, label, shown))
                record["error"] = "fetched result was not a valid {} result".format(mode)
                fetched = False
        except (OSError, ValueError) as exc:
            say("fetched result is not valid JSON: {}".format(exc))
            record["error"] = "fetched result was not valid JSON"
            fetched = False
        if not fetched:
            # A refused fetch must not linger under the result name,
            # where a later glob would mistake it for a real result.
            try:
                os.remove(local)
            except OSError:
                pass
    elif code == 0:
        record["error"] = "result could not be fetched from " + host
    if fetched:
        record["result_file"] = os.path.abspath(local)
        say("Fetched {}".format(local))
        _ssh(host, remove_remote)
    return record


def run_remote(args) -> Tuple[int, Dict[str, object]]:
    """Run every target in order. Returns (exit code, summary).

    One failed host never blocks the next one. The exit code is 0 only
    when every target ran and (for run/live) its result was fetched; a
    remote command's own non-zero code wins over a fetched file.
    """
    from . import PACKAGE_VERSION
    from .summary import SUMMARY_VERSION

    json_stdout = getattr(args, "json", None) == "-"

    def say(message: str = "") -> None:
        try:
            print(message, file=sys.stderr if json_stdout else sys.stdout, flush=True)
        except (OSError, ValueError):
            pass

    targets = parse_targets(args)
    records: List[Dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        pyz = build_pyz(os.path.join(tmp, "agentperf.pyz"))
        for index, (host, wsl, label) in enumerate(targets):
            if len(targets) > 1:
                say("\n=== host {} of {}: {}{}".format(
                    index + 1, len(targets), host, " (WSL " + wsl + ")" if wsl else ""))
            try:
                records.append(_run_one(host, wsl, label, args, pyz, say))
            except SystemExit as exc:
                message = exc.code if isinstance(exc.code, str) else "remote setup failed"
                say("error: {}: {}".format(host, message))
                records.append({"host": host, "wsl": wsl, "label": label,
                                "mode": getattr(args, "mode", "run") or "run",
                                "exit_code": None, "result_file": None,
                                "error": str(message)})
            except (OSError, subprocess.SubprocessError, RuntimeError,
                    ValueError) as exc:
                # One host's operational failure (ssh missing, scp
                # error, local disk full) is that host's result; the
                # rest of the fleet still runs and the aggregate
                # summary is still emitted. KeyboardInterrupt is not
                # caught: it stops the whole command.
                message = "{}: {}".format(type(exc).__name__, exc)
                say("error: {}: {}".format(host, message))
                records.append({"host": host, "wsl": wsl, "label": label,
                                "mode": getattr(args, "mode", "run") or "run",
                                "exit_code": None, "result_file": None,
                                "error": message})
    mode = getattr(args, "mode", "run") or "run"

    def ok(rec) -> bool:
        # Execution success only: doctor machine health lives in each
        # record's doctor.ok, separate from whether the check ran.
        if rec.get("error") or (rec.get("exit_code") or 0) != 0:
            return False
        if mode == "doctor":
            return rec.get("doctor") is not None
        return bool(rec.get("result_file"))

    code = 0 if all(ok(r) for r in records) else 1
    for rec in records:  # a remote command's own failure code wins, as before
        if rec.get("exit_code"):
            code = int(rec["exit_code"])  # type: ignore[arg-type]
            break
    summary: Dict[str, object] = {
        "command": "remote",
        "summary_version": SUMMARY_VERSION,
        "package_version": PACKAGE_VERSION,
        "mode": mode,
        "sequential": True,
        "hosts": records,
        "results": [r["result_file"] for r in records if r.get("result_file")],
        "compare_html": None,
    }
    return code, summary
