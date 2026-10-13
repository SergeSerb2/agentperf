"""Plain-text reports for the terminal."""

from __future__ import annotations

from typing import Dict, List, Optional

from . import PACKAGE_VERSION, scoring
from .advice import advise
from .registry import CATEGORY_TITLES, CATEGORY_WEIGHTS
from .util import fmt_gain, fmt_num, fmt_ratio


def _lw100_text(lw, ref_lw=None) -> str:
    if lw["seconds"] is None:
        return "LW100     not available (none of its parts were measured)"
    note = ""
    if lw["missing"]:
        note = " [estimate covers {} of {} parts; missing: {}]".format(
            len(lw["lines"]), len(lw["lines"]) + len(lw["missing"]), "; ".join(lw["missing"]))
    if ref_lw is not None and ref_lw["seconds"] is not None:
        return "LW100     {:.1f} s estimated local wait per 100 tool calls (reference {:.1f} s, same parts){}".format(
            lw["seconds"], ref_lw["seconds"], note)
    return "LW100     {:.1f} s estimated local wait per 100 tool calls{}".format(lw["seconds"], note)

_ORDER = list(CATEGORY_WEIGHTS) + ["network", "diag"]


def _system_line(result) -> str:
    s = result["system"]
    parts = [s.get("os_version") or s.get("os"), s.get("cpu_model"),
             "{} CPUs".format(s.get("logical_cpus")),
             "{} GB RAM".format(s.get("ram_gb")) if s.get("ram_gb") else None,
             s.get("fs_type"), "Python " + s.get("python", "?")]
    return " | ".join(str(p) for p in parts if p)


def run_report(result, reference: Optional[Dict[str, object]] = None) -> str:
    lines: List[str] = []
    lines.append("agentperf {} - {} ({}, result suite {})".format(
        PACKAGE_VERSION, result["label"], result["profile"], result.get("suite_version", "?")))
    lines.append(_system_line(result))
    lines.append("")
    sc = scoring.score(result, reference) if reference else None
    by_cat: Dict[str, List[str]] = {}
    for bid, e in result["benchmarks"].items():
        by_cat.setdefault(e["category"], []).append(bid)
    for cat in _ORDER:
        if cat not in by_cat:
            continue
        head = CATEGORY_TITLES.get(cat, cat)
        if sc and cat in sc["categories"]:
            head += "  - score {}".format(fmt_num(sc["categories"][cat]))
        lines.append(head)
        for bid in by_cat[cat]:
            e = result["benchmarks"][bid]
            ref_e = reference["benchmarks"].get(bid) if reference else None
            score_txt = fmt_num(sc["benchmarks"][bid]) if sc and bid in sc["benchmarks"] else ""
            lines.append("  {:<52} {:>10} {:<6} {:>10} {:>7}{}".format(
                e["title"][:52], fmt_num(e["median"]), e["unit"],
                fmt_num(ref_e["median"]) if ref_e else "", score_txt, "  noisy" if e.get("noisy") else ""))
        lines.append("")
    if result.get("skipped"):
        lines.append("Skipped: " + "; ".join("{} ({})".format(k, v) for k, v in result["skipped"].items()))
        lines.append("")
    if sc:
        lw, ref_lw = scoring.lw100_common(result, reference)
        ci = sc.get("agentmark_ci") or [None, None]
        lines.append("AgentMark {}  (90% {} {}-{}, coverage {:.0%})  vs reference '{}'".format(
            fmt_num(sc["agentmark"]), sc.get("ci_kind", "within-run interval"),
            fmt_num(ci[0]), fmt_num(ci[1]), sc["coverage"], reference["label"]))
        for warning in scoring.compatibility_warnings(result, reference):
            lines.append("Comparability: " + warning)
        if sc.get("reach"):
            lines.append("Reach     {}  (network to model APIs and CDNs)".format(fmt_num(sc["reach"])))
        lines.append(_lw100_text(lw, ref_lw))
    else:
        lines.append(_lw100_text(scoring.lw100(result)))
        lines.append("No reference profile: run `agentperf compare` with --ref to get scores.")
    tips = advise(result)
    if tips:
        lines.append("")
        lines.append("Findings")
        for t in tips:
            lines.append("  [{}] {}: {}".format(t["level"], t["topic"], t["text"]))
    if reference:
        lines.extend(["", why_summary(result, reference)])
    return "\n".join(lines)


def why_summary(result, reference) -> str:
    """Short terminal WHY: verdict, ranked causes with upper-bound gains."""
    from . import diagnose
    sc = scoring.score(result, reference)
    lines = ["Why this score", "  " + diagnose.verdict(
        sc["agentmark"], (result.get("system") or {}).get("os_family"))]
    attr = diagnose.attribution(result, reference)
    if attr["biggest_drags"]:
        d = attr["biggest_drags"][0]
        lines.append("  Biggest drag: {} at x{} vs reference (worth up to +{} AgentMark).".format(
            d["id"], fmt_ratio(d["ratio"]), fmt_gain(d["gain_points"])))
    major, minor = diagnose.partition_causes(diagnose.causes(result, reference))
    items = major[:3]
    if items:
        lines.append("  Biggest wins (gains are upper bounds and overlap):")
        for i, c in enumerate(items, 1):
            gain = ("up to +{}".format(fmt_gain(c["gain_points"]))
                    if c.get("gain_estimated") and c["gain_points"] else "gain not estimated")
            lines.append("  {}. [{}] {} ({})".format(i, c["confidence"], c["topic"], gain))
            if c["fix"]:
                lines.append("     Fix: {}".format(c["fix"][0]))
    else:
        lines.append("  No dominant software cause found; see the HTML report for per-test attribution.")
    if minor:
        lines.append("  Minor wins (each under +{}): {}".format(
            fmt_gain(diagnose.MINOR_GAIN_POINTS), "; ".join(c["topic"] for c in minor)))
    lines.append("  Full report: agentperf report <result>.json --html report.html")
    return "\n".join(lines)


def compare_report(results: List[Dict[str, object]], reference: Dict[str, object],
                   category_weights=None) -> str:
    common = scoring.common_benchmarks(results + [reference])
    labels = [r["label"] for r in results]
    width = max(10, max(len(l) for l in labels) + 1)
    head = "{:<34}".format("") + "".join("{:>{}}".format(l[:width - 1], width) for l in labels)
    lines = ["Reference: {}  (score 1000 = reference speed; higher is better)".format(reference["label"])]
    if category_weights is not None:
        lines.append("Category weights: equal (sensitivity check; the default weights differ)")
    for r in results:
        for warning in scoring.compatibility_warnings(r, reference):
            lines.append("Comparability ({}): {}".format(r["label"], warning))
    lines += ["", head]
    full = [scoring.score(r, reference, category_weights=category_weights) for r in results]
    same = [scoring.score(r, reference, restrict=common, category_weights=category_weights)
            for r in results]

    def row(name, values):
        lines.append("{:<34}".format(name) + "".join("{:>{}}".format(v, width) for v in values))

    row("AgentMark (all tests)", [fmt_num(s["agentmark"]) for s in full])
    row("  coverage", ["{:.0%}".format(s["coverage"]) for s in full])
    row("AgentMark (common tests)", [fmt_num(s["agentmark"]) for s in same])
    row("Reach (network)", [fmt_num(s["reach"]) for s in full])
    def _lw_cell(doc):
        value = scoring.lw100(doc, common)["seconds"]
        return "{:.1f}".format(value) if value is not None else "n/a"

    row("LW100 seconds (common)", [_lw_cell(r) for r in results])
    lines.append("")
    for cat in _ORDER:
        if cat == "diag":
            continue
        row(CATEGORY_TITLES[cat], [fmt_num(s["categories"].get(cat)) for s in full])
    lines.append("")
    all_ids = []
    for r in [reference] + results:
        for bid in r["benchmarks"]:
            if bid not in all_ids:
                all_ids.append(bid)
    lines.append("Raw medians")
    for bid in all_ids:
        unit = next(r["benchmarks"][bid]["unit"] for r in [reference] + results if bid in r["benchmarks"])
        row("{} ({})".format(bid, unit)[:33],
            [fmt_num(r["benchmarks"][bid]["median"]) if bid in r["benchmarks"] else "-" for r in results])
    return "\n".join(lines)
