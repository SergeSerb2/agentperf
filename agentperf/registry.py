"""Benchmark definitions and the shared run context.

A benchmark is a factory: it receives the Context, does untimed setup, and
returns a `sample()` callable (optionally with a cleanup callable). Each call
to `sample()` performs one fixed unit of work and returns its own measurement
in the benchmark's unit, so setup inside a sample can stay untimed.
"""

from __future__ import annotations

import os
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .util import rmtree

# Headline AgentMark weights. Network is scored separately as Reach.
CATEGORY_WEIGHTS = {
    "process": 25.0,   # every Bash tool call spawns a shell (79% of tool calls in real history)
    "files": 15.0,     # Read/Write/Edit tools, cat/sed, metadata walks
    "search": 15.0,    # grep, rg, glob
    "git": 15.0,       # status/diff/commit/worktree
    "compute": 15.0,   # tests, builds, scripts
    "transfer": 10.0,  # large and many-file copies, archive extraction, disk throughput
    "ipc": 5.0,        # tool output capture, local servers
}
CATEGORY_TITLES = {
    "process": "Process spawn",
    "files": "Small files",
    "search": "Search",
    "git": "Git",
    "compute": "Compute",
    "transfer": "File transfer",
    "ipc": "IPC",
    "network": "Network (Reach)",
    "diag": "Diagnostics",
}


class Bench:
    def __init__(self, bench_id: str, factory: Callable, title: str, unit: str, lower_is_better: bool,
                 models: str, requires: Sequence[str] = (), weight: float = 1.0, scored: bool = True,
                 reps: int = 5, warmup: int = 1, budget_s: float = 20.0, ops: int = 1,
                 platforms: Optional[Sequence[str]] = None):
        self.id = bench_id
        self.category = bench_id.split(".", 1)[0]
        self.factory = factory
        self.title = title
        self.unit = unit
        self.lower_is_better = lower_is_better
        self.models = models
        self.requires = tuple(requires)
        self.weight = weight
        self.scored = scored and self.category not in ("diag",)
        self.reps = reps
        self.warmup = warmup
        self.budget_s = budget_s
        self.ops = ops  # operations per sample, for per-op latency (LW100)
        self.platforms = tuple(platforms) if platforms else None

    def describe(self) -> Dict[str, object]:
        return {
            "category": self.category, "title": self.title, "unit": self.unit,
            "lower_is_better": self.lower_is_better, "models": self.models, "weight": self.weight,
            "scored": self.scored, "ops": self.ops,
        }


REGISTRY: List[Bench] = []


def bench(bench_id: str, **kw) -> Callable:
    def deco(factory: Callable) -> Callable:
        REGISTRY.append(Bench(bench_id, factory, **kw))
        return factory
    return deco


class Skip(Exception):
    """Raised by a factory when the benchmark cannot run on this machine."""


class Context:
    def __init__(self, workdir: str, corpus, tools: Dict[str, Optional[str]], os_family: str,
                 profile: str, log: Callable[[str], None]):
        self.workdir = workdir
        self.corpus = corpus
        self.tools = tools
        self.os_family = os_family
        self.profile = profile
        self.log = log
        self.notes: Dict[str, Dict[str, object]] = {}
        self._cache: Dict[str, object] = {}

    def scratch(self, name: str) -> str:
        """A fresh, empty directory under the work root."""
        path = os.path.join(self.workdir, "scratch", name)
        rmtree(path)
        os.makedirs(path)
        return path

    def need(self, *tools: str) -> Tuple[str, ...]:
        paths = []
        for tool in tools:
            path = self.tools.get(tool)
            if not path:
                raise Skip("{} not found".format(tool))
            paths.append(path)
        return tuple(paths)

    def cached(self, key: str, make: Callable[[], object]) -> object:
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    def note(self, bench_id: str, **meta) -> None:
        self.notes.setdefault(bench_id, {}).update(meta)
