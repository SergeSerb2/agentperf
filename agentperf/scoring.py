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

    Every benchmark entry needs the fields scoring and reporting read
    (category, weight, median, samples, direction, scored flag), each
    of the right type; malformed entries (lists, nulls, NaN, strings)
    are bad input with a clear message, never an AttributeError or
    TypeError deep in scoring. Fields agents read as objects must be
    objects when present, nulls included: a present-but-null field is
    malformed, not absent."""

    def bad(message):
        raise ValueError("{} in {}".format(message, source))

    if not isinstance(doc, dict) or not isinstance(doc.get("benchmarks"), dict):
        raise ValueError(
            "not an agentperf result file: {} (no benchmarks object)".format(source))
    for bid, entry in doc["benchmarks"].items():
        if not isinstance(bid, str) or not isinstance(entry, dict):
            bad("benchmark {!r} is not an object".format(bid))
        if not _finite_number(entry.get("median")):
            bad("benchmark {!r} has no finite numeric median".format(bid))
        if not isinstance(entry.get("category"), str) \
                or not entry.get("category"):
            bad("benchmark {!r} has no category".format(bid))
        if not _finite_number(entry.get("weight")):
            bad("benchmark {!r} has no finite numeric weight".format(bid))
        for key in ("lower_is_better", "scored", "noisy"):
            if key in entry and not isinstance(entry[key], bool):
                bad("benchmark {!r} has a non-boolean {}".format(bid, key))
        if "lower_is_better" not in entry or "scored" not in entry:
            bad("benchmark {!r} is missing lower_is_better/scored".format(bid))
        samples = entry.get("samples")
        if not isinstance(samples, list) or not samples \
                or not all(_finite_number(s) for s in samples):
            bad("benchmark {!r} has malformed samples".format(bid))
        for key in ("ops", "cv", "min", "max", "p10", "p90", "n", "runs"):
            if key in entry and not _finite_number(entry[key]):
                bad("benchmark {!r} has a non-numeric {}".format(bid, key))
        for key in ("title", "unit", "models"):
            if key in entry and not isinstance(entry[key], str):
                bad("benchmark {!r} has a non-text {}".format(bid, key))
        if "meta" in entry and not isinstance(entry["meta"], dict):
            bad("benchmark {!r} has a non-object meta".format(bid))
        run_samples = entry.get("run_samples")
        if "run_samples" in entry and (
                not isinstance(run_samples, list)
                or any(not isinstance(r, list)
                       or not all(_finite_number(s) for s in r)
                       for r in run_samples)):
            bad("benchmark {!r} has malformed run_samples".format(bid))
        if "run_medians" in entry and (
                not isinstance(entry["run_medians"], list)
                or not all(_finite_number(s) for s in entry["run_medians"])):
            bad("benchmark {!r} has malformed run_medians".format(bid))
    for key in ("system", "corpus", "skipped", "tools", "tool_versions",
                "scoring_config", "measurement_env", "reference_profile"):
        if key in doc and not isinstance(doc[key], dict):
            bad("field {!r} is not an object".format(key))
    if "label" in doc and not isinstance(doc["label"], str):
        bad("field 'label' is not text")
    if "schema" in doc and (not isinstance(doc["schema"], int)
                             or isinstance(doc["schema"], bool)):
        bad("field 'schema' is not a version number")
    for key in ("kind", "suite_version", "package_version", "profile",
                "started_at", "fingerprint"):
        if key in doc and not isinstance(doc[key], str):
            bad("field {!r} is not text".format(key))
    if "duration_s" in doc and not _finite_number(doc["duration_s"]):
        bad("field 'duration_s' is not a finite number")
    corpus = doc.get("corpus")
    if isinstance(corpus, dict):
        if "fixture_hash" in corpus \
                and not isinstance(corpus["fixture_hash"], str):
            bad("corpus fixture_hash is not text")
        for key in ("files", "bytes"):
            if key in corpus and not _finite_number(corpus[key]):
                bad("corpus {} is not a finite number".format(key))
    config = doc.get("scoring_config")
    if isinstance(config, dict) and "category_weights" in config:
        weights = config["category_weights"]
        if not isinstance(weights, dict) or not all(
                isinstance(k, str) and _finite_number(v)
                for k, v in weights.items()):
            bad("scoring_config category_weights is malformed")
    env = doc.get("measurement_env")
    if isinstance(env, dict) and "shell_launch" in env \
            and not isinstance(env["shell_launch"], str):
        bad("measurement_env shell_launch is not text")
    skipped = doc.get("skipped")
    if isinstance(skipped, dict) and not all(
            isinstance(v, str) for v in skipped.values()):
        bad("field 'skipped' has non-text reasons")
    for key in ("tools", "tool_versions"):
        values = doc.get(key)
        if isinstance(values, dict) and not all(
                v is None or isinstance(v, str) for v in values.values()):
            bad("field {!r} has non-text values".format(key))
    for key in ("combined_runs", "merged_runs"):
        if key in doc and (not isinstance(doc[key], list)
                           or not all(isinstance(r, dict)
                                      for r in doc[key])):
            bad("field {!r} is not a list of objects".format(key))
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
    try:
        weight = round(float(entry.get("weight")), 6)
    except (TypeError, ValueError):
        weight = 0.0
    return (entry.get("category"), weight,
            bool(entry.get("scored")), entry.get("unit"),
            bool(entry.get("lower_is_better")), entry.get("ops"))


def _method(entry):
    """How a benchmark measured, where that can differ between machines."""
    meta = entry.get("meta")
    if not isinstance(meta, dict):
        return ""
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


# The scored benchmarks that launch a shell. Each has its own effective
# shell-startup policy; they are compared independently, because one
# benchmark's recorded policy must never mask another's.
SHELL_BENCHMARKS = ("process.shell", "process.parallel")


def _classify_shell_policy(text) -> str:
    """One recorded policy string -> sanitized / inherited / unknown.

    Only the string itself is read: the first of the two policy words
    in it decides (recorded values lead with the word, as in
    "sanitized (removed: ...)" or "sanitized-equivalent ...")."""
    if not isinstance(text, str):
        return "unknown"
    lowered = text.lower()
    sanitized = lowered.find("sanitized")
    inherited = lowered.find("inherited")
    if sanitized == -1 and inherited == -1:
        return "unknown"
    if inherited == -1 or (sanitized != -1 and sanitized < inherited):
        return "sanitized"
    return "inherited"


def _doc_shell_launch(doc) -> str:
    """The document-level shell policy: measurement_env.shell_launch only.

    Other measurement_env fields (the Node spawn diagnostic) describe a
    different launch and must never classify scored shell launches."""
    env = (doc or {}).get("measurement_env")
    if isinstance(env, dict):
        return _classify_shell_policy(env.get("shell_launch"))
    return "unknown"


def _bench_shell_policy(doc, bid) -> str:
    """Effective shell-startup policy for one scored shell benchmark.

    Its own ``env_policy`` note wins; the document-level
    ``measurement_env.shell_launch`` is the fallback for benchmarks
    measured before the note existed."""
    benchmarks = (doc or {}).get("benchmarks")
    entry = benchmarks.get(bid) if isinstance(benchmarks, dict) else None
    if isinstance(entry, dict):
        meta = entry.get("meta")
        if isinstance(meta, dict):
            policy = _classify_shell_policy(meta.get("env_policy"))
            if policy != "unknown":
                return policy
    return _doc_shell_launch(doc)


def shell_env_policies(doc) -> Dict[str, str]:
    """{benchmark id: effective policy} for each scored shell benchmark
    present in *doc*. Compatibility and combine compare these per
    benchmark, never as one document-level value."""
    benchmarks = (doc or {}).get("benchmarks")
    if not isinstance(benchmarks, dict):
        return {}
    return {bid: _bench_shell_policy(doc, bid)
            for bid in SHELL_BENCHMARKS if bid in benchmarks}


def shell_env_policy(doc) -> str:
    """Effective shell-startup policy a result was measured under.

    One of ``sanitized`` (scored shell launches ran with shell-startup
    variables removed, or a recorded sanitized-equivalent: measured
    before the policy was recorded, with those variables verified unset
    on the measuring machine), ``inherited`` (startup files could run
    and be timed), ``mixed`` (the scored shell benchmarks disagree), or
    ``unknown`` (nothing recorded: files written before the policy
    existed, where equivalence cannot be confirmed). Each benchmark's
    ``env_policy`` note wins over the document-level
    ``measurement_env.shell_launch`` for that benchmark."""
    policies = shell_env_policies(doc)
    if policies:
        distinct = set(policies.values())
        return distinct.pop() if len(distinct) == 1 else "mixed"
    return _doc_shell_launch(doc)


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
    # cannot. Each scored shell benchmark's effective policy is
    # compared independently, so one benchmark's policy (or an
    # unrelated measurement_env field) can never mask another's. Two
    # files from before the policy was recorded share the
    # inherited-era code and do not warn against each other.
    mine_policies = shell_env_policies(result)
    theirs_policies = shell_env_policies(reference)
    for bid in SHELL_BENCHMARKS:
        if bid not in mine_policies or bid not in theirs_policies:
            continue
        mine_entry = (result.get("benchmarks") or {}).get(bid)
        theirs_entry = (reference.get("benchmarks") or {}).get(bid)
        mine_entry = mine_entry if isinstance(mine_entry, dict) else {}
        theirs_entry = theirs_entry if isinstance(theirs_entry, dict) \
            else {}
        if not (mine_entry.get("scored") or theirs_entry.get("scored")):
            continue
        mine_policy = mine_policies[bid]
        theirs_policy = theirs_policies[bid]
        if mine_policy == theirs_policy:
            continue
        if "unknown" in (mine_policy, theirs_policy):
            side = "result" if mine_policy == "unknown" else "reference"
            warnings.append(
                "shell environment policy is unknown for the {} ({}, "
                "written before the policy was recorded): if BASH_ENV or "
                "ENV was set when it ran, its shell tests timed startup "
                "scripts, so shell scores may not be the same work as "
                "the other file's.".format(side, bid))
        else:
            warnings.append(
                "shell environment policies differ for {} (result: {}, "
                "reference: {}): scored shell launches did different "
                "work, so shell scores are not strictly comparable.".format(
                    bid, mine_policy, theirs_policy))
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
