"""Harness command construction and streamed process execution."""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .parsers import PARSERS

HARNESSES = ["claude", "codex"]


def find_binary(harness):
    """Resolve the harness CLI via PATH (handles .exe/.cmd/.ps1 on Windows)."""
    return shutil.which(harness)


def harness_version(harness):
    binary = find_binary(harness)
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, "--version"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, encoding="utf-8",
                              errors="replace", timeout=30)
    except Exception:
        return None
    out = (proc.stdout or "").strip().splitlines()
    return out[0].strip() if out else None


def build_command(harness, opts):
    """Return argv for one harness run. The prompt always goes via stdin."""
    binary = find_binary(harness) or harness
    if harness == "claude":
        cmd = [binary, "-p", "--output-format", "stream-json", "--verbose",
               "--model", opts["claude_model"],
               "--permission-mode", "bypassPermissions",
               "--no-session-persistence"]
        if not opts["realistic"]:
            # Skip user settings/hooks and all MCP servers not passed here.
            cmd += ["--setting-sources", "project", "--strict-mcp-config"]
        return cmd
    if harness == "codex":
        cmd = [binary, "exec", "--json", "--ephemeral",
               "-s", opts.get("codex_sandbox") or "workspace-write",
               # Bare value: Codex reads non-TOML values as plain strings, and quotes
               # get mangled when Windows runs the codex.cmd shim through cmd.exe.
               "-c", "model_reasoning_effort=%s" % opts["codex_effort"]]
        if opts.get("codex_model"):
            cmd += ["-m", opts["codex_model"]]
        if not opts["realistic"]:
            cmd += ["--ignore-user-config", "--ignore-rules"]
        cmd += ["-"]  # read prompt from stdin
        return cmd
    raise ValueError("unknown harness: %s" % harness)


def display_command(cmd):
    if sys.platform == "win32":
        return subprocess.list2cmdline(cmd)
    return shlex.join(cmd)


def run_streamed(harness, cmd, prompt, cwd, timeout):
    """Run *cmd*, stamp each stdout line on arrival, return timing dict.

    Returns parser result plus wall_s, exit_code, error, stderr_tail.
    """
    parser = PARSERS[harness](time.monotonic())
    result = {"exit_code": None, "error": None, "stderr_tail": ""}
    err_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    t0 = time.monotonic()
    parser.t0 = t0
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=err_file, encoding="utf-8", errors="replace")
    except OSError as exc:
        result["error"] = "launch failed: %s" % exc
        result.update(parser.result(0.0))
        result["wall_s"] = 0.0
        err_file.close()
        return result

    def feed_stdin():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass

    def read_stdout():
        try:
            for line in proc.stdout:
                parser.feed(time.monotonic(), line)
        except (OSError, ValueError):
            pass

    writer = threading.Thread(target=feed_stdin)
    reader = threading.Thread(target=read_stdout)
    writer.daemon = True
    reader.daemon = True
    writer.start()
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        result["error"] = "timed out after %s s" % timeout
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
    wall = time.monotonic() - t0
    reader.join(timeout=10)

    err_file.seek(0)
    lines = [ln for ln in err_file.read().splitlines() if "stale arg0" not in ln]
    err_file.close()
    result["stderr_tail"] = "\n".join(lines[-5:])
    result["exit_code"] = proc.returncode
    if proc.returncode not in (0, None) and result["error"] is None:
        result["error"] = "exit code %s" % proc.returncode
        if parser.errors:
            result["error"] += ": " + parser.errors[-1][:300]
        elif result["stderr_tail"]:
            result["error"] += ": " + result["stderr_tail"][-300:]
    result.update(parser.result(wall))
    result["wall_s"] = wall
    return result
