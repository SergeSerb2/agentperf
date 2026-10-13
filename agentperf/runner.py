"""Run the system suite and produce one result document."""

from __future__ import annotations

import datetime
import fnmatch
import math
import os
import shutil
import traceback
import uuid
from typing import Callable, Dict, List, Optional

from . import PACKAGE_VERSION, SCHEMA_VERSION, SUITE_VERSION, benches  # noqa: F401  (registers benchmarks)
from . import env, tools
from .corpus import Corpus
from .registry import REGISTRY, Context, Skip
from .util import now, os_family, rmtree, summarize

MIN_FREE_GB = 1.5
NOISY_CV = 0.15

PROFILES = {
    # reps multiplier, max warmups, budget multiplier
    "quick": (0.5, 1, 0.5),
    "standard": (1.0, 2, 1.0),
}


def default_workdir() -> str:
    return os.path.join(os.path.expanduser("~"), ".agentperf", "work")


def _selected(bench_id: str, only: Optional[List[str]], exclude: Optional[List[str]]) -> bool:
    def hit(patterns):
        return any(fnmatch.fnmatch(bench_id, p) or bench_id.startswith(p + ".") for p in patterns)
    if only and not hit(only):
        return False
    return not (exclude and hit(exclude))


LOCK_NAME = ".agentperf-run.lock"


def _acquire_lock(workdir: str) -> str:
    """One run per workdir: concurrent runs would share the corpus and scratch."""
    path = os.path.join(workdir, LOCK_NAME)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(
            "Another agentperf run is using {} (lock file {} exists). "
            "Wait for it to finish, use a different --workdir, or delete the "
            "lock file if no run is active.".format(workdir, path))
    os.write(fd, "{}\n".format(os.getpid()).encode("ascii"))
    os.close(fd)
    return path


def run_suite(workdir: str, profile: str = "standard", label: Optional[str] = None,
              only: Optional[List[str]] = None, exclude: Optional[List[str]] = None,
              fetch: bool = False, clean: bool = False,
              log: Callable[[str], None] = print) -> Dict[str, object]:
    os.makedirs(workdir, exist_ok=True)
    free_gb = shutil.disk_usage(workdir).free / 1e9
    if free_gb < MIN_FREE_GB:
        raise SystemExit("Only {:.1f} GB free at {}; agentperf needs {:.1f} GB.".format(
            free_gb, workdir, MIN_FREE_GB))
    lock = _acquire_lock(workdir)
    try:
        return _run_locked(workdir, profile, label, only, exclude, fetch, clean, log)
    finally:
        rmtree(os.path.join(workdir, "scratch"))
        try:
            os.remove(lock)
        except OSError:
            pass


def _run_locked(workdir: str, profile: str, label: Optional[str],
                only: Optional[List[str]], exclude: Optional[List[str]],
                fetch: bool, clean: bool,
                log: Callable[[str], None]) -> Dict[str, object]:
    reps_mult, max_warmup, budget_mult = PROFILES[profile]
    started = datetime.datetime.now(datetime.timezone.utc)
    t_start = now()
    if fetch:
        log("Fetching pinned tools...")
        tools.fetch_tools(log)
    found = tools.find_tools()
    log("Collecting system facts...")
    system = env.collect(workdir)
    corpus = Corpus(os.path.join(workdir, "corpus")).ensure(log)
    ctx = Context(workdir, corpus, found, os_family(), profile, log)

    results: Dict[str, Dict[str, object]] = {}
    skipped: Dict[str, str] = {}
    chosen = [b for b in REGISTRY if _selected(b.id, only, exclude)]
    for index, b in enumerate(chosen, 1):
        if b.platforms and ctx.os_family not in b.platforms:
            skipped[b.id] = "not on this platform"
            continue
        prefix = "[{:>2}/{}] {:<24}".format(index, len(chosen), b.id)
        try:
            made = b.factory(ctx)
        except Skip as exc:
            skipped[b.id] = str(exc)
            log("{} skipped: {}".format(prefix, exc))
            continue
        except Exception as exc:  # a broken benchmark must not end the run
            skipped[b.id] = "setup error: {}".format(exc)
            log("{} setup error: {}".format(prefix, exc))
            log(traceback.format_exc(limit=3))
            continue
        sample, cleanup = made if isinstance(made, tuple) else (made, None)
        reps = max(3, int(math.ceil(b.reps * reps_mult)))
        samples: List[float] = []
        try:
            for _ in range(min(b.warmup, max_warmup)):
                t_warm = now()
                value = float(sample())
                if now() - t_warm > 5.0:  # slow machine: keep this run, skip more warmups
                    samples.append(value)
                    break
            t_bench = now()
            while len(samples) < reps:
                samples.append(float(sample()))
                if now() - t_bench > b.budget_s * budget_mult and len(samples) >= 3:
                    break
        except Exception as exc:
            skipped[b.id] = "error: {}".format(exc)
            log("{} error: {}".format(prefix, exc))
        finally:
            if cleanup:
                try:
                    cleanup()
                except Exception:
                    pass
        if b.id in skipped:
            continue
        stats = summarize(samples)
        entry = dict(b.describe())
        entry.update(stats)
        entry["samples"] = [round(s, 6) for s in samples]
        entry["meta"] = ctx.notes.get(b.id, {})
        entry["noisy"] = stats["cv"] > NOISY_CV
        results[b.id] = entry
        log("{} {:>10.3f} {:<7} cv {:>4.0%}{}".format(prefix, stats["median"], b.unit, stats["cv"],
                                                    "  (noisy)" if entry["noisy"] else ""))

    rmtree(os.path.join(workdir, "scratch"))
    if hasattr(os, "getloadavg"):
        system["load_avg_end"] = [round(x, 2) for x in os.getloadavg()]
    doc = {
        "schema": SCHEMA_VERSION,
        "kind": "system",
        "suite_version": SUITE_VERSION,
        "package_version": PACKAGE_VERSION,
        "label": label or system.get("hostname"),
        "run_id": uuid.uuid4().hex[:12],
        "started_at": started.isoformat(timespec="seconds"),
        "duration_s": round(now() - t_start, 1),
        "profile": profile,
        "corpus": {"files": len(corpus.files), "bytes": corpus.total_bytes,
                   "fixture_hash": corpus.fixture_hash},
        "system": system,
        "tools": {k: v for k, v in found.items() if v},
        "tool_versions": tools.tool_versions(found),
        "benchmarks": results,
        "skipped": skipped,
    }
    if clean:
        rmtree(workdir)
    return doc
