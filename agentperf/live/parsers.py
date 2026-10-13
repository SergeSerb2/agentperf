"""Stream parsers: split wall time into startup / tool / model time.

Each parser is fed (monotonic_timestamp, line) pairs as lines arrive.
Tool time for a call = arrival of its result event minus arrival of its
call event; the run's tool_s is the union of those intervals, so parallel
calls are not double counted (tool_breakdown keeps per-tool sums).
Startup = first event arrival minus process launch time.
"""
from __future__ import annotations

import json


def _union_length(intervals):
    """Seconds covered by at least one interval. Parallel tool calls overlap, so a
    plain sum would count the same wall-clock time more than once."""
    total, end = 0.0, None
    for a, b in sorted(intervals):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


class _Base(object):
    def __init__(self, t0):
        self.t0 = t0
        self.first_t = None
        self.pending = {}       # tool id -> (start_t, tool_name)
        self.intervals = []     # (start_t, end_t) per finished tool call
        self.tool_s = 0.0
        self.tool_calls = 0
        self.breakdown = {}     # name -> {"count": int, "s": float}
        self.turns = 0
        self.model = None
        self.api_s = None
        self.tokens = {"input": None, "output": None, "cache_read": None}
        self.cost_usd = None
        self.errors = []        # error messages reported inside the stream

    def _seen_event(self, t):
        if self.first_t is None:
            self.first_t = t

    def _tool_start(self, t, tool_id, name):
        if tool_id is not None:
            self.pending[tool_id] = (t, name)

    def _tool_end(self, t, tool_id):
        start = self.pending.pop(tool_id, None)
        if start is None:
            return
        dt = max(0.0, t - start[0])
        self.intervals.append((start[0], start[0] + dt))
        self.tool_s = _union_length(self.intervals)
        self.tool_calls += 1
        slot = self.breakdown.setdefault(start[1], {"count": 0, "s": 0.0})
        slot["count"] += 1
        slot["s"] += dt

    def feed(self, t, line):
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if isinstance(event, dict):
            self._seen_event(t)
            self._handle(t, event)

    def _handle(self, t, event):
        raise NotImplementedError

    def result(self, wall_s):
        startup = (self.first_t - self.t0) if self.first_t is not None else wall_s
        startup = max(0.0, min(startup, wall_s))
        return {
            "startup_s": startup,
            "tool_s": self.tool_s,
            "model_s": max(0.0, wall_s - startup - self.tool_s),
            "api_s": self.api_s,
            "tool_calls": self.tool_calls,
            "turns": self.turns,
            "tool_breakdown": dict(
                (k, {"count": v["count"], "s": v["s"]})
                for k, v in sorted(self.breakdown.items())),
            "tokens": dict(self.tokens),
            "cost_usd": self.cost_usd,
            "model": self.model,
        }


class ClaudeParser(_Base):
    """Claude Code `--output-format stream-json` events."""

    def _handle(self, t, event):
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            self.model = event.get("model") or self.model
        elif etype == "assistant":
            self.turns += 1  # refined by result.num_turns below
            msg = event.get("message") or {}
            self.model = msg.get("model") or self.model
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    self._tool_start(t, block.get("id"), block.get("name") or "?")
        elif etype == "user":
            msg = event.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        self._tool_end(t, block.get("tool_use_id"))
        elif etype == "result":
            if event.get("is_error"):
                self.errors.append(str(event.get("result") or event.get("subtype") or "error"))
            if event.get("num_turns") is not None:
                self.turns = event["num_turns"]
            if event.get("duration_api_ms") is not None:
                self.api_s = event["duration_api_ms"] / 1000.0
            if event.get("total_cost_usd") is not None:
                self.cost_usd = event["total_cost_usd"]
            usage = event.get("usage") or {}
            self.tokens = {
                "input": usage.get("input_tokens"),
                "output": usage.get("output_tokens"),
                "cache_read": usage.get("cache_read_input_tokens"),
            }


_CODEX_TOOL_ITEMS = {
    "command_execution": "Bash",
    "file_change": "FileChange",
    "mcp_tool_call": "McpToolCall",
    "web_search": "WebSearch",
    "collab_tool_call": "CollabToolCall",
}


class CodexParser(_Base):
    """Codex `exec --json` events (item.started / item.completed)."""

    def _handle(self, t, event):
        etype = event.get("type") or ""
        if etype == "turn.started":
            self.turns += 1
        elif etype == "error":
            self.errors.append(str(event.get("message", "")))
        elif etype == "turn.failed":
            self.errors.append(str((event.get("error") or {}).get("message", "")))
        elif etype == "item.started":
            item = event.get("item") or {}
            name = _CODEX_TOOL_ITEMS.get(item.get("type"))
            if name:
                self._tool_start(t, item.get("id"), name)
        elif etype == "item.completed":
            item = event.get("item") or {}
            if item.get("id") in self.pending:
                self._tool_end(t, item.get("id"))
            elif item.get("type") == "file_change":
                # file_change may complete without a started event
                self.tool_calls += 1
                slot = self.breakdown.setdefault("FileChange", {"count": 0, "s": 0.0})
                slot["count"] += 1
        elif etype == "turn.completed":
            usage = event.get("usage") or {}
            if usage:
                self.tokens = {
                    "input": usage.get("input_tokens"),
                    "output": usage.get("output_tokens"),
                    "cache_read": usage.get("cached_input_tokens"),
                }


PARSERS = {"claude": ClaudeParser, "codex": CodexParser}
