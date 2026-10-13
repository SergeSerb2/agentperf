"""Process spawn: the tax paid by every Bash tool call, hook, and MCP server."""

from __future__ import annotations

import subprocess
import sys

from ..registry import bench
from ..util import IS_WINDOWS, now

_NO_WINDOW = {"creationflags": 0x08000000} if IS_WINDOWS else {}


def _spawn_ms(argv, count: int) -> float:
    t0 = now()
    for _ in range(count):
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, **_NO_WINDOW)
    return (now() - t0) * 1000.0 / count


def _shell_argv(ctx, command: str):
    (shell,) = ctx.need("shell")
    if IS_WINDOWS:  # Git Bash, the shell Claude Code uses on Windows
        return [shell, "--noprofile", "--norc", "-c", command]
    return [shell, "-c", command]


@bench("process.shell", title="Shell spawn (sh -c true; Git Bash on Windows)", unit="ms",
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
        for proc in procs:
            proc.wait()
        return (now() - t0) * 1000.0

    return sample


@bench("diag.login_shell", title="Login shell with your dotfiles ($SHELL -lic exit)", unit="ms",
       lower_is_better=True, models="Harnesses that start login shells pay this per command",
       reps=3, platforms=("macos", "linux", "wsl"))
def login_shell(ctx):
    import os
    shell = os.environ.get("SHELL") or "/bin/sh"
    ctx.note("diag.login_shell", shell=shell)
    return lambda: _spawn_ms([shell, "-lic", "exit"], 3)


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
