"""``agentperf live``: run real Claude Code / Codex sessions on fixed tasks.

Splits each run's wall time into local tool time and model/network time.
Live runs cost tokens; the default is one run per task per harness.
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import socket
import sys

from .. import PACKAGE_VERSION, SCHEMA_VERSION, SUITE_VERSION
from ..util import rmtree
from . import fixture as fixture_mod
from . import harness as harness_mod
from . import tasks as tasks_mod

__all__ = ["add_parser", "run"]


def add_parser(subparsers):
    parser = subparsers.add_parser(
        "live", help="run real Claude Code / Codex sessions on fixed tasks")
    parser.add_argument("--harness", default="claude,codex",
                        help="comma list of harnesses (default: claude,codex)")
    parser.add_argument("--tasks", default=None,
                        help="comma list of tasks (default: all)")
    parser.add_argument("--claude-model", default="sonnet")
    parser.add_argument("--codex-model", default=None,
                        help="default: the user's Codex config default")
    parser.add_argument("--codex-effort", default="low")
    parser.add_argument("--codex-sandbox", default="workspace-write",
                        choices=["read-only", "workspace-write", "danger-full-access"],
                        help="Codex sandbox. On Windows, workspace-write blocks every command in "
                             "non-interactive runs unless the elevated sandbox is set up; use "
                             "danger-full-access there (the fixture is a throwaway copy)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="seconds per task (default: 300)")
    parser.add_argument("--workdir", default=os.path.join("~", ".agentperf", "live"))
    parser.add_argument("--out", default=None,
                        help="JSON output path (default: results/live-<label>-<ts>.json)")
    parser.add_argument("--label", default=None, help="default: hostname")
    parser.add_argument("--realistic", action="store_true",
                        help="keep the user's own settings, hooks, and MCP servers")
    parser.add_argument("--dry-run", action="store_true",
                        help="build fixtures, print versions and commands, spend no tokens")
    parser.add_argument("--clean", action="store_true",
                        help="delete each run's fixture copy after verification "
                             "(default: keep them under the workdir for inspection)")
    parser.set_defaults(func=run)


def _opts(args):
    return {
        "claude_model": args.claude_model,
        "codex_model": args.codex_model,
        "codex_effort": args.codex_effort,
        "codex_sandbox": args.codex_sandbox,
        "realistic": args.realistic,
    }


def _split(value, default, valid, what):
    names = default if value is None else [v.strip() for v in value.split(",") if v.strip()]
    bad = [n for n in names if n not in valid]
    if bad:
        raise ValueError("unknown %s: %s (valid: %s)" % (what, ", ".join(bad), ", ".join(valid)))
    return names


def run(args):
    try:
        harnesses = _split(args.harness, harness_mod.HARNESSES,
                           harness_mod.HARNESSES, "harness")
        task_names = _split(args.tasks, tasks_mod.TASK_NAMES,
                            tasks_mod.TASK_NAMES, "task")
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    label = args.label or socket.gethostname() or "unknown"
    workdir = os.path.abspath(os.path.expanduser(args.workdir))
    os.makedirs(workdir, exist_ok=True)
    base = os.path.join(workdir, "fixture-base")
    buggy = os.path.join(workdir, "fixture-base-buggy")
    if not os.path.isdir(os.path.join(base, ".git")):
        fixture_mod.build_fixture(base)
    if "fix" in task_names and not os.path.isdir(os.path.join(buggy, ".git")):
        fixture_mod.build_fixture(buggy, seed_bug=True)
    opts = _opts(args)

    versions = {}
    for name in harnesses:
        versions[name] = harness_mod.harness_version(name)

    if args.dry_run:
        print("fixture: %s" % base)
        for name in harnesses:
            binary = harness_mod.find_binary(name)
            print("%s: %s (%s)" % (name, versions[name] or "NOT FOUND", binary))
            cmd = harness_mod.build_command(name, opts)
            for task in task_names:
                print("  [%s] cwd=<copy of fixture> $ %s"
                      % (task, harness_mod.display_command(cmd)))
        missing = [n for n in harnesses if not versions[n]]
        return 1 if missing else 0

    missing = [n for n in harnesses if not harness_mod.find_binary(n)]
    if missing:
        available = [n for n in harnesses if n not in missing]
        if not available:
            print("error: no harness CLI found on PATH (looked for: %s). "
                  "Install Claude Code and/or Codex first." % ", ".join(harnesses),
                  file=sys.stderr)
            return 2
        print("warning: CLI not found for %s; running %s only"
              % (", ".join(missing), ", ".join(available)))
        harnesses = available

    sessions = len(harnesses) * len(task_names)
    print("Live runs use your own Claude Code / Codex accounts and COST TOKENS: "
          "%d session(s) (%s x %s), up to %s s each."
          % (sessions, ", ".join(harnesses), ", ".join(task_names), args.timeout))
    print("Sandboxes: claude runs with --permission-mode bypassPermissions; "
          "codex runs with -s %s. Both work only in throwaway fixture copies "
          "under %s." % (opts["codex_sandbox"], workdir))

    started = datetime.datetime.now(datetime.timezone.utc)
    runs = []
    for name in harnesses:
        cmd = harness_mod.build_command(name, opts)
        model_label = (args.claude_model if name == "claude"
                       else (args.codex_model or "config-default"))
        for task in task_names:
            dest = os.path.join(workdir, "run-%s-%s" % (name, task))
            fixture_mod.fresh_copy(buggy if task == "fix" else base, dest)
            print("running %s/%s ..." % (name, task))
            timed = harness_mod.run_streamed(
                name, cmd, tasks_mod.PROMPTS[task], dest, args.timeout)
            success, detail = tasks_mod.verify(task, dest)
            if timed["error"]:
                success = False
                detail = "%s; %s" % (timed["error"], detail)
            runs.append({
                "harness": name,
                "harness_version": versions[name],
                "model": timed.pop("model") or model_label,
                "sandbox": args.codex_sandbox if name == "codex" else "bypassPermissions",
                "task": task,
                "success": success,
                "verify_detail": detail,
                "wall_s": round(timed.pop("wall_s"), 3),
                "startup_s": round(timed.pop("startup_s"), 3),
                "tool_s": round(timed.pop("tool_s"), 3),
                "model_s": round(timed.pop("model_s"), 3),
                "api_s": timed.pop("api_s"),
                "tool_calls": timed.pop("tool_calls"),
                "turns": timed.pop("turns"),
                "tool_breakdown": dict(
                    (k, {"count": v["count"], "s": round(v["s"], 3)})
                    for k, v in timed.pop("tool_breakdown").items()),
                "tokens": timed.pop("tokens"),
                "cost_usd": timed.pop("cost_usd"),
                "exit_code": timed.pop("exit_code"),
                "error": timed.pop("error"),
            })
            r = runs[-1]
            print("  %s wall=%.1fs tool=%.1fs model=%.1fs tools=%d"
                  % ("PASS" if success else "FAIL",
                     r["wall_s"], r["tool_s"], r["model_s"], r["tool_calls"]))
            if getattr(args, "clean", False):
                rmtree(dest)

    out = args.out
    if not out:
        out = os.path.join(
            "results", "live-%s-%s.json" % (label, started.strftime("%Y%m%dT%H%M%S")))
    out_dir = os.path.dirname(os.path.abspath(out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    payload = {
        "schema": SCHEMA_VERSION,
        "kind": "live",
        "suite_version": SUITE_VERSION,
        "package_version": PACKAGE_VERSION,
        "label": label,
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": {
            "os": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "runs": runs,
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print("wrote %s" % out)
    if not getattr(args, "clean", False):
        print("run fixture copies kept under %s (delete them any time, or use --clean)" % workdir)
    return 0 if all(r["success"] for r in runs) else 1
