"""Machine-readable summaries for agents and scripts.

Every summary is one JSON object with ``command`` and ``summary_version``
fields, so a caller can tell summaries apart without scraping human text.
Summaries are derived views of a result file: they never change scoring,
and they are NOT anonymized (only ``agentperf share`` output is fit to
post). See docs/result-schema.md for the field reference.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Callable, Dict, List, Optional

from . import PACKAGE_VERSION, SUITE_VERSION, scoring

SUMMARY_VERSION = 1


def _num(value) -> Optional[float]:
    return round(float(value), 3) if value is not None else None


def _marks(value) -> Optional[float]:
    """AgentMark-style score: one decimal is plenty and keeps diffs stable."""
    return round(float(value), 1) if value is not None else None


def _interval(ci) -> Optional[List[float]]:
    if not ci:
        return None
    return [_marks(ci[0]), _marks(ci[1])]


def _lw100_fields(lw) -> Dict[str, object]:
    """LW100 coverage disclosure: which parts the seconds cover or omit.

    The human reports have always named this; a bare seconds number in
    JSON would present a partial mix as the whole wait."""
    return {
        "lw100_covered_components": [line["label"] for line in lw["lines"]],
        "lw100_missing_components": list(lw["missing"]),
    }


def error_summary(command: str, message: str, exit_code: int) -> Dict[str, object]:
    """The documented failure object for --json paths.

    Emitted instead of a result summary when a command fails, so a
    caller reading stdout or the summary file always gets one JSON
    object, never an empty stream."""
    return {
        "command": command,
        "summary_version": SUMMARY_VERSION,
        "package_version": PACKAGE_VERSION,
        "ok": False,
        "error": message,
        "exit_code": exit_code,
    }


def result_summary(result, reference, result_path: Optional[str] = None,
                   html_path: Optional[str] = None) -> Dict[str, object]:
    """Summary of one system-suite result, scored against *reference*."""
    from . import diagnose
    from .advice import advise

    system = result.get("system") or {}
    doc: Dict[str, object] = {
        "command": "result",
        "summary_version": SUMMARY_VERSION,
        "package_version": PACKAGE_VERSION,
        "suite_version": result.get("suite_version", SUITE_VERSION),
        "label": result.get("label"),
        "profile": result.get("profile"),
        "result_file": os.path.abspath(result_path) if result_path else None,
        "html_file": os.path.abspath(html_path) if html_path else None,
        "started_at": result.get("started_at"),
        "duration_s": result.get("duration_s"),
        "fingerprint": result.get("fingerprint"),
        "corpus_fixture_hash": (result.get("corpus") or {}).get("fixture_hash"),
        "system": {k: system.get(k) for k in (
            "os_family", "os_version", "cpu_model", "logical_cpus", "ram_gb",
            "fs_type", "python", "wsl") if system.get(k) not in (None, "", [], False)},
        "benchmarks_run": len(result.get("benchmarks") or {}),
        "skipped": result.get("skipped") or {},
        "findings": advise(result),
    }
    if reference is None:
        lw_only = scoring.lw100(result)
        doc.update({"reference": None, "agentmark": None, "reach": None,
                    "lw100_seconds": _num(lw_only["seconds"]),
                    "verdict": diagnose.verdict(None, system.get("os_family")),
                    "top_causes": [], "biggest_drag": None,
                    "comparability_warnings": []})
        doc.update(_lw100_fields(lw_only))
        return doc
    sc = scoring.score(result, reference)
    lw, ref_lw = scoring.lw100_common(result, reference)
    attr = diagnose.attribution(result, reference)
    drags = attr.get("biggest_drags") or []
    major, minor = diagnose.partition_causes(diagnose.causes(result, reference))
    doc.update({
        "reference": {"label": reference.get("label"),
                      "fingerprint": reference.get("fingerprint")},
        "agentmark": _marks(sc.get("agentmark")),
        "agentmark_ci": _interval(sc.get("agentmark_ci")),
        "ci_kind": sc.get("ci_kind"),
        "coverage": _num(sc.get("coverage")),
        "reach": _marks(sc.get("reach")),
        "reach_ci": _interval(sc.get("reach_ci")),
        "lw100_seconds": _num(lw["seconds"]),
        "lw100_reference_seconds": _num(ref_lw["seconds"]),
        "categories": {k: _marks(v) for k, v in (sc.get("categories") or {}).items()},
        "comparability_warnings": scoring.compatibility_warnings(result, reference),
        "verdict": diagnose.verdict(sc.get("agentmark"), system.get("os_family")),
        "biggest_drag": ({"id": drags[0]["id"], "ratio": _num(drags[0]["ratio"]),
                          "gain_points": _marks(drags[0]["gain_points"])} if drags else None),
        "top_causes": [{
            "topic": c["topic"], "confidence": c["confidence"],
            "gain_estimated": bool(c.get("gain_estimated")),
            "gain_points": _marks(c.get("gain_points")),
            "fix": c["fix"][0] if c.get("fix") else "",
        } for c in major[:3]],
        "minor_causes": [c["topic"] for c in minor],
    })
    doc.update(_lw100_fields(lw))
    return doc


def compare_summary(results, reference, category_weights=None,
                    html_path: Optional[str] = None) -> Dict[str, object]:
    """Summary of a compare: every machine scored the same two ways.

    ``agentmark`` uses all of a machine's tests; ``agentmark_common`` uses
    only the tests every compared document shares, which is the number a
    ranking must use.
    """
    common = scoring.common_benchmarks(list(results) + [reference])
    machines = []
    for doc in results:
        full = scoring.score(doc, reference, category_weights=category_weights)
        same = scoring.score(doc, reference, restrict=common,
                             category_weights=category_weights)
        doc_lw = scoring.lw100(doc, common)
        machine = {
            "label": doc.get("label"),
            "agentmark": _marks(full.get("agentmark")),
            "coverage": _num(full.get("coverage")),
            "agentmark_common": _marks(same.get("agentmark")),
            "reach": _marks(full.get("reach")),
            "lw100_common_seconds": _num(doc_lw["seconds"]),
            "categories": {k: _marks(v) for k, v in (full.get("categories") or {}).items()},
            "comparability_warnings": scoring.compatibility_warnings(doc, reference),
        }
        machine.update(_lw100_fields(doc_lw))
        machines.append(machine)
    return {
        "command": "compare",
        "summary_version": SUMMARY_VERSION,
        "package_version": PACKAGE_VERSION,
        "reference": {"label": reference.get("label"),
                      "fingerprint": reference.get("fingerprint")},
        "weights": "equal" if category_weights is not None else "default",
        "common_tests": sorted(common),
        "html_file": os.path.abspath(html_path) if html_path else None,
        "machines": machines,
    }


def _assert_finite(node, where: str = "summary") -> None:
    """Reject NaN/Infinity anywhere in a summary before serialization.

    Python's json would otherwise write them as bare ``NaN`` /
    ``Infinity`` tokens, which are not JSON and break strict parsers."""
    if isinstance(node, float):
        if not math.isfinite(node):
            raise ValueError("non-finite number in {}".format(where))
    elif isinstance(node, dict):
        for key, value in node.items():
            _assert_finite(value, "{}.{}".format(where, key))
    elif isinstance(node, (list, tuple)):
        for i, value in enumerate(node):
            _assert_finite(value, "{}[{}]".format(where, i))


def emit(doc: Dict[str, object], dest: Optional[str],
         say: Callable[[str], None] = print) -> None:
    """Write a summary to a file, or to stdout when *dest* is ``-``.

    Callers that pass ``-`` keep stdout clean (human progress goes to
    stderr), so the JSON is the only thing on stdout and always parses.
    Serialization is strict: a non-finite number raises instead of
    writing invalid JSON.
    """
    if not dest:
        return
    _assert_finite(doc)
    text = json.dumps(doc, indent=2, allow_nan=False) + "\n"
    if dest == "-":
        sys.stdout.write(text)
        return
    parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(parent, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    say("Summary JSON {}".format(os.path.abspath(dest)))
