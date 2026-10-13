"""Process spawn: the tax paid by every Bash tool call, hook, and MCP server."""

from __future__ import annotations

import json
import subprocess
import sys

from ..registry import bench
from ..util import IS_WINDOWS, now, run

_NO_WINDOW = {"creationflags": 0x08000000} if IS_WINDOWS else {}


SPAWN_TIMEOUT_S = 30.0


def _spawn_ms(argv, count: int) -> float:
    """Time *count* launches. A launch that fails or hangs fails the test:
    a broken tool must never earn a startup score."""
    t0 = now()
    for _ in range(count):
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=SPAWN_TIMEOUT_S, **_NO_WINDOW)
        if proc.returncode != 0:
            raise RuntimeError("{} exited with code {}".format(argv[0], proc.returncode))
    return (now() - t0) * 1000.0 / count


def _shell_argv(ctx, command: str):
    """The scored shell: bash with no profile files, on every OS.

    Agents start bash or zsh, not dash. Scoring /bin/sh would score dash on
    the Ubuntu reference against bash elsewhere. Git Bash on Windows is the
    same shell family (and the shell Claude Code uses there). Where no bash
    exists, fall back to sh and record it, so the difference is visible.
    """
    (shell,) = ctx.need("shell")
    name = shell.replace("\\", "/").rsplit("/", 1)[-1].lower()
    kind = "bash" if "bash" in name else "sh"
    ctx.note("process.shell", shell_kind=kind, shell=name)
    ctx.note("process.parallel", shell_kind=kind, shell=name)
    if kind == "bash":
        return [shell, "--noprofile", "--norc", "-c", command]
    return [shell, "-c", command]


@bench("process.shell", title="Shell spawn (bash --noprofile --norc -c true)", unit="ms",
       lower_is_better=True, models="Every Bash tool call starts a shell", reps=7, warmup=1)
def shell_spawn(ctx):
    argv = _shell_argv(ctx, "true")
    return lambda: _spawn_ms(argv, 30)


@bench("process.exec", title="Native process spawn (true; cmd.exe on Windows)", unit="ms",
       lower_is_better=True, models="Spawning small native tools and npm .cmd shims", reps=7)
def exec_spawn(ctx):
    (exe,) = ctx.need("exec")
    argv = [exe, "/d", "/c", "rem"] if IS_WINDOWS else [exe]
    return lambda: _spawn_ms(argv, 30)


@bench("process.python", title="Python interpreter start (python -c pass)", unit="ms",
       lower_is_better=True, models="python3 scripts and test runners (12% of shell commands)",
       reps=5, weight=0.75)
def python_spawn(ctx):
    ctx.note("process.python", python=sys.version.split()[0])
    argv = [sys.executable, "-c", "pass"]
    return lambda: _spawn_ms(argv, 10)


@bench("process.node", title="Node start (node -e 0)", unit="ms", lower_is_better=True,
       models="MCP servers, hooks, npx and JS tooling", requires=("node",), reps=5, weight=0.75)
def node_spawn(ctx):
    (node,) = ctx.need("node")
    argv = [node, "-e", "0"]
    return lambda: _spawn_ms(argv, 8)


@bench("process.parallel", title="32 shells spawned at once", unit="ms", lower_is_better=True,
       models="Parallel tool calls and subagents", reps=5)
def parallel_spawn(ctx):
    argv = _shell_argv(ctx, "true")

    def sample():
        t0 = now()
        procs = [subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, **_NO_WINDOW) for _ in range(32)]
        try:
            deadline = t0 + 120.0
            for proc in procs:
                proc.wait(timeout=max(0.1, deadline - now()))
            bad = [p.returncode for p in procs if p.returncode != 0]
            if bad:
                raise RuntimeError("{} of 32 shells failed (first exit code {})".format(
                    len(bad), bad[0]))
        except BaseException:
            for proc in procs:  # reap everything, whatever happened
                try:
                    proc.kill()
                except OSError:
                    pass
            for proc in procs:
                try:
                    proc.wait(timeout=10)
                except Exception:
                    pass
            raise
        return (now() - t0) * 1000.0

    return sample


_NODE_SPAWN_SCRIPT = r"""
const {spawnSync} = require('child_process');
const shell = process.argv[1];
const args = JSON.parse(process.argv[2]);
const n = 30;
const t0 = performance.now();
for (let i = 0; i < n; i++) {
  const r = spawnSync(shell, args, {stdio: 'ignore'});
  if (r.error) { console.error(String(r.error)); process.exit(1); }
  if (r.status !== 0) { console.error('shell exited ' + r.status); process.exit(1); }
}
console.log(JSON.stringify({ms: (performance.now() - t0) / n}));
"""


@bench("diag.node_spawn", title="Shell spawn from Node (spawnSync)", unit="ms",
       lower_is_better=True,
       models="What a Node-based harness pays per shell start, without the Python "
              "subprocess overhead included in process.shell",
       requires=("node", "shell"), reps=5)
def node_spawn_diag(ctx):
    (node,) = ctx.need("node")
    argv = _shell_argv(ctx, "true")
    shell, shell_args = argv[0], argv[1:]

    def sample():
        proc = run([node, "-e", _NODE_SPAWN_SCRIPT, shell, json.dumps(shell_args)], timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:200])
        return float(json.loads(proc.stdout.decode().strip().splitlines()[-1])["ms"])

    return sample


@bench("diag.login_shell", title="Login shell as Codex runs it ($SHELL -lc exit)", unit="ms",
       lower_is_better=True,
       models="Codex runs each command in a login shell (zsh -lc), which loads "
              "login files (.zprofile) but not interactive ones (.zshrc)",
       reps=3, platforms=("macos", "linux", "wsl"))
def login_shell(ctx):
    import os
    shell = os.environ.get("SHELL") or "/bin/sh"
    ctx.note("diag.login_shell", shell=shell.replace("\\", "/").rsplit("/", 1)[-1],
             mode="-lc (login files only, as Codex runs it)")
    return lambda: _spawn_ms([shell, "-lc", "exit"], 3)


@bench("diag.powershell", title="PowerShell spawn (-NoProfile)", unit="ms", lower_is_better=True,
       models="Codex runs each command through PowerShell on Windows", reps=3,
       platforms=("windows",))
def powershell_spawn(ctx):
    (ps,) = ctx.need("powershell")
    return lambda: _spawn_ms([ps, "-NoLogo", "-NoProfile", "-Command", "exit"], 5)


@bench("diag.powershell_profile", title="PowerShell spawn with your profile", unit="ms",
       lower_is_better=True, models="PowerShell sessions that load the user profile", reps=3,
       platforms=("windows",))
def powershell_profile_spawn(ctx):
    (ps,) = ctx.need("powershell")
    return lambda: _spawn_ms([ps, "-NoLogo", "-Command", "exit"], 3)


@bench("diag.claude_start", title="claude --version", unit="ms", lower_is_better=True,
       models="Harness CLI start", requires=("claude",), reps=3)
def claude_start(ctx):
    (exe,) = ctx.need("claude")
    return lambda: _spawn_ms([exe, "--version"], 3)


@bench("diag.codex_start", title="codex --version", unit="ms", lower_is_better=True,
       models="Harness CLI start", requires=("codex",), reps=3)
def codex_start(ctx):
    (exe,) = ctx.need("codex")
    return lambda: _spawn_ms([exe, "--version"], 3)
