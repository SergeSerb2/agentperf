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


def _validate_selectors(only: Optional[List[str]], exclude: Optional[List[str]]) -> None:
    """Every selector must match at least one known benchmark id or category."""
    ids = [b.id for b in REGISTRY]
    for kind, patterns in (("--only", only or []), ("--exclude", exclude or [])):
        for pattern in patterns:
            if not any(fnmatch.fnmatch(i, pattern) or i.startswith(pattern + ".") for i in ids):
                raise SystemExit(
                    "{} selector {!r} matches no benchmark. Run `agentperf list` "
                    "to see the ids and categories.".format(kind, pattern))


LOCK_NAME = ".agentperf-run.lock"
MARKER_NAME = ".agentperf-owned"
# Everything agentperf may create or delete inside an owned work directory.
_OWNED_ENTRIES = ("corpus", "repo", "scratch", "corpus.tar.gz", "empty.gitconfig",
                  "no-hooks", LOCK_NAME, MARKER_NAME)


def workdir_owned(workdir: str) -> bool:
    return os.path.isfile(os.path.join(workdir, MARKER_NAME))


def _legacy_agentperf_dir(workdir: str) -> bool:
    """True for a work directory made by agentperf before ownership markers:
    it holds a corpus manifest that only agentperf writes."""
    import json as _json
    try:
        with open(os.path.join(workdir, "corpus", "corpus.json"), encoding="utf-8") as fh:
            data = _json.load(fh)
        return bool(data.get("fixture_hash")) and "files" in data
    except (OSError, ValueError, AttributeError):
        return False


def prepare_workdir(workdir: str) -> None:
    """Claim *workdir* for agentperf, or refuse before touching anything.

    A missing directory is created. An existing empty directory, or one that
    carries the ownership marker from an earlier run, is claimed. Any other
    non-empty directory is refused: agentperf must never delete a user's
    files (it removes corpus/, repo/, and scratch/ inside its workdir).
    """
    if os.path.exists(workdir) and not os.path.isdir(workdir):
        raise SystemExit("--workdir {} exists and is not a directory.".format(workdir))
    if os.path.isdir(workdir) and not workdir_owned(workdir) and _legacy_agentperf_dir(workdir):
        pass  # a pre-0.3.0 work directory: claim it below by writing the marker
    elif os.path.isdir(workdir) and not workdir_owned(workdir) and os.listdir(workdir):
        raise SystemExit(
            "--workdir {} is not empty and was not created by agentperf "
            "(no {} marker). Refusing to use it, because a run deletes its "
            "own corpus, repo, and scratch folders inside the work directory. "
            "Pick an empty directory, a new path, or the default "
            "(~/.agentperf/work).".format(workdir, MARKER_NAME))
    os.makedirs(workdir, exist_ok=True)
    marker = os.path.join(workdir, MARKER_NAME)
    if not os.path.exists(marker):
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write("This directory is managed by agentperf. A run may delete "
                     "corpus/, repo/, and scratch/ inside it.\n")


def _clean_owned(workdir: str) -> None:
    """Remove only agentperf-owned entries, then the directory if it is empty."""
    if not workdir_owned(workdir):
        return
    for name in os.listdir(workdir):
        if name in _OWNED_ENTRIES or name.startswith("corpus-") and name.endswith(".tar.gz"):
            path = os.path.join(workdir, name)
            if os.path.isdir(path) and not os.path.islink(path):
                rmtree(path)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
    try:
        os.rmdir(workdir)
    except OSError:
        pass  # not empty: something not owned is inside; leave it alone


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
    _validate_selectors(only, exclude)
    prepare_workdir(workdir)
    free_gb = shutil.disk_usage(workdir).free / 1e9
    if free_gb < MIN_FREE_GB:
        raise SystemExit("Only {:.1f} GB free at {}; agentperf needs {:.1f} GB.".format(
            free_gb, workdir, MIN_FREE_GB))
    lock = _acquire_lock(workdir)
    try:
        doc = _run_locked(workdir, profile, label, only, exclude, fetch, log)
        # All cleanup happens while the lock is held, so a following run
        # cannot create scratch that this run then deletes.
        rmtree(os.path.join(workdir, "scratch"))
        if clean:
            _clean_owned(workdir)
        return doc
    finally:
        try:
            rmtree(os.path.join(workdir, "scratch"))
        except Exception:
            pass
        try:
            os.remove(lock)
        except OSError:
            pass


def _run_locked(workdir: str, profile: str, label: Optional[str],
                only: Optional[List[str]], exclude: Optional[List[str]],
                fetch: bool,
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

    # An empty or all-failed run is a failure, never a silent success.
    applicable_scored = [b for b in chosen
                         if b.scored and (not b.platforms or ctx.os_family in b.platforms)]
    if not results:
        raise SystemExit(
            "No benchmark produced a result ({} skipped or failed). "
            "Run `agentperf doctor` to check tools and the work directory.".format(len(skipped)))
    if applicable_scored and not any(b.id in results for b in applicable_scored):
        raise SystemExit(
            "No scored benchmark succeeded ({} skipped or failed); there is "
            "nothing to score. Run `agentperf doctor` to check tools.".format(len(skipped)))
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
    from . import scoring
    doc["fingerprint"] = scoring.fingerprint(doc)
    return doc
