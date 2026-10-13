"""Turn raw results into AgentMark (local machine), Reach (network), and LW100.

Per benchmark: ratio = reference / value for times, value / reference for rates,
so a ratio of 2.0 always means twice as fast as the reference. Category score
= 1000 x weighted geometric mean of ratios. AgentMark = 1000 x weighted
geometric mean of category ratios. A missing benchmark is never imputed; it
lowers coverage instead.
"""

from __future__ import annotations

import json
import random
from typing import Dict, Iterable, List, Optional, Set

from .registry import CATEGORY_WEIGHTS
from .util import bootstrap_medians, geomean, percentile

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


def load(path: str) -> Dict[str, object]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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


def compatibility_warnings(result, reference) -> List[str]:
    """Reasons these two documents may not be strictly comparable.

    Comparability rests on what was actually measured: the corpus fixture
    hash and the scored benchmark definitions recorded in each file. Suite
    version strings are printed in reports but are not the key: the bundled
    0.1.0 profiles carry definitions identical to the 0.2.x suite.
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
    return warnings


def score(result, reference, restrict: Optional[Set[str]] = None, rounds: int = 300,
          seed: int = 7, category_weights=None) -> Dict[str, object]:
    cats = _scorable(result, reference, restrict)
    ratios = {b: _ratio(result["benchmarks"][b], reference["benchmarks"][b]) for b in cats}
    out = _aggregate(ratios, result, cats, category_weights)
    out["benchmarks"] = {b: 1000 * r for b, r in ratios.items()}
    out["coverage"] = coverage(result, reference, cats, category_weights)
    out["compared"] = sorted(cats)
    # Bootstrap: resample this machine's samples per benchmark; the reference stays fixed.
    rng = random.Random(seed)
    boot = {b: bootstrap_medians(result["benchmarks"][b]["samples"], rounds, rng) for b in cats}
    marks, reaches = [], []
    for i in range(rounds):
        rr = {b: _ratio(result["benchmarks"][b], reference["benchmarks"][b], boot[b][i]) for b in cats}
        agg = _aggregate(rr, result, cats, category_weights)
        if agg["agentmark"]:
            marks.append(agg["agentmark"])
        if agg["reach"]:
            reaches.append(agg["reach"])
    out["agentmark_ci"] = [percentile(marks, 0.05), percentile(marks, 0.95)] if marks else None
    out["reach_ci"] = [percentile(reaches, 0.05), percentile(reaches, 0.95)] if reaches else None
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
    return {"seconds": total, "lines": lines, "missing": missing}


def common_benchmarks(results: List[Dict[str, object]]) -> Set[str]:
    sets = [set(r["benchmarks"]) for r in results]
    return set.intersection(*sets) if sets else set()
