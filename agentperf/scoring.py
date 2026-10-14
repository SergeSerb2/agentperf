"""Turn raw results into AgentMark (local machine), Reach (network), and LW100.

Per benchmark: ratio = reference / value for times, value / reference for rates,
so a ratio of 2.0 always means twice as fast as the reference. Category score
= 1000 x weighted geometric mean of ratios. AgentMark = 1000 x weighted
geometric mean of category ratios. A missing benchmark is never imputed; it
lowers coverage instead.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Dict, Iterable, List, Optional, Set

from .registry import CATEGORY_WEIGHTS
from .util import bootstrap_medians, geomean, median, percentile

# Local wait per 100 tool calls. Counts come from 6,416 Claude Code tool calls
# on the author's machine (Bash 79%, Read 9%, Write+Edit 3.4%, other 8.6%).
# Inside Bash: sed/cat 22%, python3 12%, git 8%, node/npx 2%. grep appears in
# 44 of every 100 calls, but only 17% of those search a folder tree (grep -r);
# the rest filter a pipe or named files and cost little beyond the shell start.
LW100_MIX = [
    ("Shell start for each Bash call", 79.0, [("process.shell", 1.0)]),
    ("File reads (Read tool, cat, sed)", 26.0, [("files.read", 1.0)]),
    ("Folder-tree grep (grep -r)", 7.5, [("search.grep", 1.0)]),
    ("python3 starts", 9.5, [("process.python", 1.0)]),
    ("git commands", 6.3, [("git.status", 0.5), ("git.diff", 0.3), ("git.log", 0.2)]),
    ("node / npx starts", 1.6, [("process.node", 1.0)]),
    ("Edits and writes", 3.4, [("files.edit", 1.0)]),
    ("Commits", 1.0, [("git.commit", 1.0)]),
]


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def validate_result(doc, source: str = "result") -> Dict[str, object]:
    """Check a result document's shape before any code trusts it.

    Every benchmark entry needs a finite numeric median; malformed
    entries (lists, nulls, NaN, strings) are bad input with a clear
    message, never an AttributeError deep in scoring. Fields agents
    read as objects must be objects when present."""
    if not isinstance(doc, dict) or not isinstance(doc.get("benchmarks"), dict):
        raise ValueError(
            "not an agentperf result file: {} (no benchmarks object)".format(source))
    for bid, entry in doc["benchmarks"].items():
        if not isinstance(entry, dict):
            raise ValueError(
                "benchmark {!r} in {} is not an object".format(bid, source))
        if not _finite_number(entry.get("median")):
            raise ValueError(
                "benchmark {!r} in {} has no finite numeric median".format(bid, source))
        samples = entry.get("samples")
        if samples is not None and (
                not isinstance(samples, list)
                or not all(_finite_number(s) for s in samples)):
            raise ValueError(
                "benchmark {!r} in {} has malformed samples".format(bid, source))
        for key in ("weight", "ops", "cv", "min", "max", "p10", "p90", "n"):
            if entry.get(key) is not None and not _finite_number(entry[key]):
                raise ValueError(
                    "benchmark {!r} in {} has a non-numeric {}".format(
                        bid, source, key))
    for key in ("system", "corpus", "skipped", "tools", "tool_versions",
                "scoring_config", "measurement_env"):
        if doc.get(key) is not None and not isinstance(doc[key], dict):
            raise ValueError(
                "field {!r} in {} is not an object".format(key, source))
    return doc


def _reject_constant(name: str):
    raise ValueError("non-finite number {!r} is not valid in a result file".format(name))


def load(path: str) -> Dict[str, object]:
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh, parse_constant=_reject_constant)
    return validate_result(doc, path)


def _ratio(entry: Dict[str, object], ref: Dict[str, object], value: Optional[float] = None) -> float:
    v = float(entry["median"]) if value is None else value
    r = float(ref["median"])
    return r / v if entry["lower_is_better"] else v / r


def _scorable(result, reference, restrict: Optional[Set[str]] = None) -> Dict[str, str]:
    """bench id -> category for benchmarks scored in both documents."""
    out = {}
    for bid, entry in result["benchmarks"].items():
        ref = reference["benchmarks"].get(bid)
        if not ref or not entry.get("scored") or not ref.get("scored"):
            continue
        if restrict is not None and bid not in restrict:
            continue
        if float(entry["median"]) <= 0 or float(ref["median"]) <= 0:
            continue
        out[bid] = entry["category"]
    return out


def equal_category_weights() -> Dict[str, float]:
    """Every category weighted the same (for sensitivity checks, never the default)."""
    return {c: 1.0 for c in CATEGORY_WEIGHTS}


def _aggregate(ratios: Dict[str, float], result, cats: Dict[str, str],
               category_weights=None) -> Dict[str, object]:
    weights = category_weights or CATEGORY_WEIGHTS
    by_cat: Dict[str, List[str]] = {}
    for bid, cat in cats.items():
        by_cat.setdefault(cat, []).append(bid)
    cat_ratio = {}
    for cat, ids in by_cat.items():
        cat_ratio[cat] = geomean([ratios[i] for i in ids],
                                 [float(result["benchmarks"][i]["weight"]) for i in ids])
    local = [c for c in cat_ratio if c in weights]
    agentmark = 1000 * geomean([cat_ratio[c] for c in local],
                               [weights[c] for c in local]) if local else None
    reach = 1000 * cat_ratio["network"] if "network" in cat_ratio else None
    return {"agentmark": agentmark, "reach": reach,
            "categories": {c: 1000 * r for c, r in cat_ratio.items()}}


def coverage(result, reference, cats: Dict[str, str], category_weights=None) -> float:
    """Share of the reference's headline weight that this result also scores."""
    total = got = 0.0
    for cat, cw in (category_weights or CATEGORY_WEIGHTS).items():
        ref_ids = [b for b, e in reference["benchmarks"].items() if e.get("scored") and e["category"] == cat]
        ref_w = sum(float(reference["benchmarks"][b]["weight"]) for b in ref_ids)
        if not ref_w:
            continue
        have = sum(float(reference["benchmarks"][b]["weight"]) for b in ref_ids if b in cats)
        total += cw
        got += cw * have / ref_w
    return got / total if total else 0.0


def _definition(entry):
    return (entry.get("category"), round(float(entry.get("weight") or 0), 6),
            bool(entry.get("scored")), entry.get("unit"),
            bool(entry.get("lower_is_better")), entry.get("ops"))


def _method(entry):
    """How a benchmark measured, where that can differ between machines."""
    meta = entry.get("meta") or {}
    parts = []
    for key in ("method", "impl", "shell_kind"):
        if meta.get(key):
            parts.append("{}={}".format(key, meta[key]))
    return ";".join(parts)


def scoring_config_snapshot(category_weights=None) -> Dict[str, object]:
    """The scoring configuration a result was produced under.

    Stored in the document so a later reader can tell which category
    weights its fingerprint was computed with, even after the defaults
    change. Results written before this field existed carry no snapshot;
    they are read with the current weights, which is what their stored
    fingerprints were computed with at the time.
    """
    weights = category_weights or CATEGORY_WEIGHTS
    return {"category_weights": {k: float(v) for k, v in sorted(weights.items())}}


def shell_env_policy(doc) -> str:
    """Effective shell-startup policy a result was measured under.

    One of ``sanitized`` (scored shell launches ran with shell-startup
    variables removed, or a recorded sanitized-equivalent: measured
    before the policy was recorded, with those variables verified unset
    on the measuring machine), ``inherited`` (startup files could run
    and be timed), or ``unknown`` (nothing recorded: files written
    before the policy existed, where equivalence cannot be confirmed).
    The per-benchmark ``env_policy`` note wins over the document-level
    ``measurement_env`` summary."""
    benchmarks = (doc or {}).get("benchmarks") or {}
    for bid in ("process.shell", "process.parallel"):
        meta = (benchmarks.get(bid) or {}).get("meta") or {}
        policy = str(meta.get("env_policy") or "").lower()
        if "sanitized" in policy:
            return "sanitized"
        if "inherited" in policy:
            return "inherited"
    env = (doc or {}).get("measurement_env")
    text = " ".join(str(v) for v in env.values()).lower() \
        if isinstance(env, dict) else str(env or "").lower()
    if "sanitized" in text:
        return "sanitized"
    if "inherited" in text:
        return "inherited"
    return "unknown"


def _weights_for(doc):
    config = (doc or {}).get("scoring_config") or {}
    recorded = config.get("category_weights")
    if isinstance(recorded, dict) and recorded:
        try:
            return {str(k): float(v) for k, v in recorded.items()}
        except (TypeError, ValueError):
            pass
    return CATEGORY_WEIGHTS


def fingerprint(doc, restrict=None) -> str:
    """Measurement/scoring fingerprint: one short hash of what was measured.

    Covers the corpus fixture, every scored benchmark's definition and
    title, its recorded measurement method, and the category weights. Two
    results are comparable only when corpus, definitions, methods, and
    fingerprint all match (see compatibility_warnings). Version strings
    alone never decide comparability. ``restrict`` limits the hash to a
    set of benchmark ids, so two documents can be fingerprinted over the
    tests they share.
    """
    h = hashlib.sha256()
    h.update(("corpus=" + str((doc.get("corpus") or {}).get("fixture_hash") or "")).encode())
    # The document's own recorded weights, so two documents made under
    # different weights fingerprint differently in any later reader.
    h.update(repr(sorted(_weights_for(doc).items())).encode())
    for bid in sorted((doc.get("benchmarks") or {})):
        if restrict is not None and bid not in restrict:
            continue
        entry = doc["benchmarks"][bid]
        if not entry.get("scored"):
            continue
        h.update(repr((bid, _definition(entry), entry.get("title"), _method(entry))).encode())
    return h.hexdigest()[:16]


def compatibility_warnings(result, reference) -> List[str]:
    """Reasons these two documents may not be strictly comparable.

    The one comparability contract, used in every report and doc: scores
    compare only when the corpus fixture hash, the scored benchmark
    definitions, the measurement methods, and the measurement/scoring
    fingerprint all match, against the same reference file. Version
    strings are printed for context but never decide comparability.
    """
    warnings: List[str] = []
    mine = (result.get("corpus") or {}).get("fixture_hash")
    theirs = (reference.get("corpus") or {}).get("fixture_hash")
    if mine and theirs and mine != theirs:
        warnings.append("corpus fixture hashes differ ({} vs {}): the test inputs "
                        "were not identical, so scores are not comparable.".format(mine, theirs))
    if result.get("schema") != reference.get("schema"):
        warnings.append("result schema versions differ ({} vs {}); unknown fields "
                        "may be read differently.".format(result.get("schema"), reference.get("schema")))
    differ = []
    for bid, entry in (result.get("benchmarks") or {}).items():
        ref_entry = (reference.get("benchmarks") or {}).get(bid)
        if ref_entry and (entry.get("scored") or ref_entry.get("scored")) \
                and _definition(entry) != _definition(ref_entry):
            differ.append(bid)
    if differ:
        shown = ", ".join(sorted(differ)[:5]) + (" and {} more".format(len(differ) - 5)
                                                 if len(differ) > 5 else "")
        warnings.append("scored benchmark definitions differ for {}: scores are not "
                        "strictly comparable.".format(shown))
    method_differ = []
    for bid, entry in (result.get("benchmarks") or {}).items():
        ref_entry = (reference.get("benchmarks") or {}).get(bid)
        if ref_entry and (entry.get("scored") or ref_entry.get("scored")) \
                and _method(entry) != _method(ref_entry):
            method_differ.append("{} ({} vs {})".format(
                bid, _method(entry) or "unrecorded", _method(ref_entry) or "unrecorded"))
    if method_differ:
        shown = "; ".join(sorted(method_differ)[:3]) + (
            " and {} more".format(len(method_differ) - 3) if len(method_differ) > 3 else "")
        extra = ""
        if any(d.startswith("transfer.read") for d in method_differ):
            extra = (" transfer.read is about 2% of AgentMark; compare that "
                     "test only between machines that used the same cold-read method.")
        warnings.append("measurement methods differ for {}.{}".format(shown, extra))
    # The shell-startup policy is part of the measurement method: an
    # inherited BASH_ENV times startup scripts, a sanitized launch
    # cannot. Two files from before the policy was recorded share the
    # inherited-era code and do not warn against each other.
    mine_policy = shell_env_policy(result)
    theirs_policy = shell_env_policy(reference)
    if mine_policy != theirs_policy:
        if "unknown" in (mine_policy, theirs_policy):
            side = "result" if mine_policy == "unknown" else "reference"
            warnings.append(
                "shell environment policy is unknown for the {} (written "
                "before the policy was recorded): if BASH_ENV or ENV was "
                "set when it ran, its shell tests timed startup scripts, "
                "so shell scores may not be the same work as the other "
                "file's.".format(side))
        else:
            warnings.append(
                "shell environment policies differ (result: {}, reference: "
                "{}): scored shell launches did different work, so shell "
                "scores are not strictly comparable.".format(
                    mine_policy, theirs_policy))
    # A stored fingerprint must match the document it travels with,
    # recomputed under the document's own recorded scoring config.
    for side, doc in (("result", result), ("reference", reference)):
        stored = (doc or {}).get("fingerprint")
        if stored and (doc or {}).get("benchmarks"):
            try:
                recomputed = fingerprint(doc)
            except Exception:
                recomputed = None
            if recomputed is not None and stored != recomputed:
                warnings.append("the {}'s stored fingerprint ({}) does not match "
                                "its contents ({}): the file was edited or written "
                                "under a different scoring config, so scores are "
                                "not strictly comparable.".format(side, stored, recomputed))
    mine_weights = _weights_for(result)
    theirs_weights = _weights_for(reference)
    if mine_weights != theirs_weights:
        differ_cats = sorted(c for c in set(mine_weights) | set(theirs_weights)
                             if mine_weights.get(c) != theirs_weights.get(c))
        warnings.append("recorded category weights differ ({}); the two files were "
                        "scored under different weights, so scores are not strictly "
                        "comparable.".format(", ".join(differ_cats[:4])))
    # Fingerprint over the shared scored tests only: a missing test lowers
    # coverage; it must not look like a different measurement.
    shared = {b for b, e in (result.get("benchmarks") or {}).items()
              if e.get("scored") and (reference.get("benchmarks") or {}).get(b, {}).get("scored")}
    if shared and not differ and not method_differ:
        mine_fp = fingerprint(result, shared)
        theirs_fp = fingerprint(reference, shared)
        if mine_fp != theirs_fp:
            warnings.append("measurement/scoring fingerprints differ ({} vs {}): what was "
                            "measured or how it was weighted changed, so scores are not "
                            "strictly comparable.".format(mine_fp, theirs_fp))
    return warnings


def score(result, reference, restrict: Optional[Set[str]] = None, rounds: int = 300,
          seed: int = 7, category_weights=None) -> Dict[str, object]:
    cats = _scorable(result, reference, restrict)
    ratios = {b: _ratio(result["benchmarks"][b], reference["benchmarks"][b]) for b in cats}
    out = _aggregate(ratios, result, cats, category_weights)
    out["benchmarks"] = {b: 1000 * r for b, r in ratios.items()}
    out["coverage"] = coverage(result, reference, cats, category_weights)
    out["compared"] = sorted(cats)
    # Bootstrap the actual estimator; the reference stays fixed. For a
    # combined result the point estimate is the median of per-run medians,
    # so resample hierarchically: runs with replacement, then samples
    # within each chosen run, then the median of those run medians.
    # Pooling samples would estimate a different quantity and can exclude
    # the point estimate.
    rng = random.Random(seed)

    # Per-benchmark run samples, aligned by run index. An empty list is
    # a run that did not measure this benchmark; it keeps its slot so
    # run identity survives across benchmarks.
    def aligned_runs(entry):
        runs = entry.get("run_samples")
        if not runs or len(runs) < 2:
            return None
        clean = [[float(s) for s in r] if r else [] for r in runs]
        return clean if sum(1 for r in clean if r) >= 2 else None

    runs_by_bench = {b: aligned_runs(result["benchmarks"][b]) for b in cats}
    combined = any(runs_by_bench[b] is not None for b in cats)
    out["ci_kind"] = "bootstrap over runs and samples" if combined else "within-run interval"
    n_runs = max([len(r) for r in runs_by_bench.values() if r] or [0])

    boot = {b: [] for b in cats}
    for _round in range(rounds):
        # Draw the run indices ONCE per round and use them for every
        # benchmark. Runs are whole-machine states (a busy run is slow
        # at everything at once); drawing per benchmark would break
        # that correlation and understate the interval.
        drawn = [rng.randrange(n_runs) for _i in range(n_runs)] if n_runs else []
        for b in cats:
            entry = result["benchmarks"][b]
            clean = runs_by_bench[b]
            if clean is None:
                samples = entry["samples"]
                n = len(samples)
                boot[b].append(median([samples[rng.randrange(n)]
                                       for _i in range(n)]))
                continue
            medians = []
            for idx in drawn:
                if idx < len(clean) and clean[idx]:
                    chosen = clean[idx]
                    medians.append(median([chosen[rng.randrange(len(chosen))]
                                           for _i in range(len(chosen))]))
            boot[b].append(median(medians) if medians else float(entry["median"]))
    marks, reaches = [], []
    for i in range(rounds):
        rr = {b: _ratio(result["benchmarks"][b], reference["benchmarks"][b], boot[b][i]) for b in cats}
        agg = _aggregate(rr, result, cats, category_weights)
        if agg["agentmark"]:
            marks.append(agg["agentmark"])
        if agg["reach"]:
            reaches.append(agg["reach"])

    def interval(values, point):
        if not values:
            return None
        lo, hi = percentile(values, 0.05), percentile(values, 0.95)
        if point:  # an interval must never exclude its own point estimate
            lo, hi = min(lo, point), max(hi, point)
        return [lo, hi]

    out["agentmark_ci"] = interval(marks, out.get("agentmark"))
    out["reach_ci"] = interval(reaches, out.get("reach"))
    return out


def per_op_ms(entry: Dict[str, object]) -> float:
    return float(entry["median"]) / float(entry.get("ops") or 1)


def lw100(result, restrict: Optional[Iterable[str]] = None) -> Dict[str, object]:
    """Seconds of local wait per 100 agent tool calls, with the per-line breakdown."""
    allowed = set(restrict) if restrict is not None else None
    lines, total, missing = [], 0.0, []
    for label, count, parts in LW100_MIX:
        ms = 0.0
        ok = True
        for bid, mult in parts:
            entry = result["benchmarks"].get(bid)
            if entry is None or (allowed is not None and bid not in allowed):
                ok = False
                break
            ms += mult * per_op_ms(entry)
        if not ok:
            missing.append(label)
            continue
        seconds = count * ms / 1000.0
        lines.append({"label": label, "count": count, "ms_each": ms, "seconds": seconds})
        total += seconds
    # Nothing measured is "not available", never a fake 0.0 s.
    return {"seconds": total if lines else None, "lines": lines, "missing": missing}


def lw100_common(result, reference):
    """LW100 for both docs on the same component set, so they compare.

    Returns (mine, theirs); each is an lw100() dict restricted to the
    benchmarks both documents have."""
    both = set(result["benchmarks"]) & set(reference["benchmarks"])
    return lw100(result, both), lw100(reference, both)


def common_benchmarks(results: List[Dict[str, object]]) -> Set[str]:
    sets = [set(r["benchmarks"]) for r in results]
    return set.intersection(*sets) if sets else set()
