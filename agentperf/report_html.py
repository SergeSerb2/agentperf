"""Self-contained HTML comparison report (inline SVG, no external assets)."""

from __future__ import annotations

import datetime
import html
import json
import math
from typing import Dict, List, Optional

from . import PACKAGE_VERSION, SUITE_VERSION, scoring
from .advice import advise
from .registry import CATEGORY_TITLES, CATEGORY_WEIGHTS
from .util import fmt_gain, fmt_num, fmt_ratio

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
TRANSFER = [("transfer.read", "Read (cold)"), ("transfer.write", "Write (flushed)"),
            ("transfer.copy", "Copy 256 MB"), ("transfer.copy_tree", "Copy 6,000 files"),
            ("transfer.extract", "Extract .tar.gz")]


def esc(text) -> str:
    return html.escape(str(text), quote=True)


# ---------------------------------------------------------------- data model

class Machine:
    def __init__(self, doc, reference, common, estimated=False, low=None, high=None,
                 category_weights=None):
        self.doc = doc
        self.label = doc["label"]
        self.estimated = estimated
        self.score = scoring.score(doc, reference, category_weights=category_weights)
        self.common = scoring.score(doc, reference, restrict=common,
                                    category_weights=category_weights)
        self.shown = self.score  # compare pages switch this to the common-test score
        self.warnings = scoring.compatibility_warnings(doc, reference)
        self.lw = scoring.lw100(doc, common)
        self.range = None
        if estimated and low and high:
            lo = scoring.score(low, reference, category_weights=category_weights)["agentmark"]
            hi = scoring.score(high, reference, category_weights=category_weights)["agentmark"]
            self.range = (min(lo, hi), max(lo, hi))
        elif self.score.get("agentmark_ci"):
            self.range = tuple(self.score["agentmark_ci"])

    @property
    def short_system(self) -> str:
        s = self.doc.get("system", {})
        cpu = str(s.get("cpu_model") or "")
        for noise in ("12th Gen ", "Intel(R) ", "Core(TM) ", "(R)", "(TM)", " CPU", "AMD ", "Intel "):
            cpu = cpu.replace(noise, "")
        cpu = cpu.split(" @ ")[0].strip()
        fam = s.get("os_family", "")
        osname = {"macos": "macOS", "windows": "Windows", "wsl": "WSL2", "linux": "Linux"}.get(fam, "")
        bits = [cpu, "{} CPUs".format(s["logical_cpus"]) if s.get("logical_cpus") else None, osname]
        return " · ".join(b for b in bits if b)

    @property
    def system_line(self) -> str:
        s = self.doc.get("system", {})
        bits = [s.get("cpu_model"), "{} CPUs".format(s.get("logical_cpus")) if s.get("logical_cpus") else None,
                "{} GB".format(s.get("ram_gb")) if s.get("ram_gb") else None,
                s.get("os_version"), s.get("fs_type")]
        return " · ".join(str(b) for b in bits if b)


def _estimate_docs(path: Optional[str], template) -> List[Dict[str, object]]:
    """Expand an estimates file into result-shaped documents (mid, pessimistic, optimistic)."""
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    out = []
    for system in spec["systems"]:
        docs = {}
        for which in ("mid", "pessimistic", "optimistic"):
            benches = {}
            for bid, model in system["benchmarks"].items():
                base = template["benchmarks"].get(bid)
                if not base:
                    continue
                entry = {k: base[k] for k in ("category", "title", "unit", "lower_is_better", "weight",
                                              "scored", "ops", "models")}
                value = model[which]
                entry.update({"median": value, "samples": [value], "cv": 0.0, "noisy": False,
                              "meta": {"basis": model.get("basis", "")}})
                benches[bid] = entry
            docs[which] = {"label": system["label"], "system": system.get("system", {}),
                           "benchmarks": benches, "skipped": {}, "estimate_notes": system.get("notes", []),
                           "estimated": True}
        out.append(docs)
    return out


# ---------------------------------------------------------------- SVG pieces

def _bar_chart(rows, unit_fmt, ref_value=None, ref_label="", width=720, log=False) -> str:
    """Horizontal bars. rows: [(label, value, sublabel, estimated, (lo, hi) or None, tip)]."""
    row_h, top, left, right = 38, 10, 220, 90
    plot_w = width - left - right
    height = top + row_h * len(rows) + 28
    values = [r[1] for r in rows if r[1]] + [x for r in rows if r[4] for x in r[4]]
    vmax = max(values + ([ref_value] if ref_value else [])) * 1.08
    vmin = min(values) * 0.8 if log else 0.0

    def x(v):
        if log:
            return left + plot_w * (math.log10(v) - math.log10(vmin)) / (math.log10(vmax) - math.log10(vmin))
        return left + plot_w * v / vmax

    parts = ['<svg class="chart" viewBox="0 0 {} {}" role="img">'.format(width, height)]
    parts.append('<line class="axis" x1="{0}" x2="{0}" y1="{1}" y2="{2}"/>'.format(left, top - 4, height - 24))
    if ref_value:
        rx = x(ref_value)
        parts.append('<line class="refline" x1="{0:.1f}" x2="{0:.1f}" y1="{1}" y2="{2}"/>'.format(rx, top - 4, height - 24))
        parts.append('<text class="muted small" x="{:.1f}" y="{}" text-anchor="middle">{}</text>'.format(
            rx, height - 8, esc(ref_label)))
    for i, (label, value, sub, est, rng, tip) in enumerate(rows):
        y = top + i * row_h
        parts.append('<text class="label" x="{}" y="{}" text-anchor="end">{}</text>'.format(left - 10, y + 15, esc(label)))
        if sub:
            parts.append('<text class="muted small" x="{}" y="{}" text-anchor="end">{}</text>'.format(left - 10, y + 28, esc(sub)))
        if not value:
            parts.append('<text class="muted small" x="{}" y="{}">not measured</text>'.format(left + 6, y + 18))
            continue
        x0, x1 = (x(vmin) if log else left), x(value)
        w = max(2.0, x1 - x0)
        fill = "url(#hatch)" if est else "var(--series-1)"
        parts.append('<g class="hit" tabindex="0" data-tip="{}">'.format(esc(tip)))
        parts.append('<rect x="{:.1f}" y="{}" width="{:.1f}" height="20" fill="transparent"/>'.format(x0, y, plot_w))
        parts.append('<path class="bar{}" d="{}" fill="{}"/>'.format(" est" if est else "", _rounded_bar(x0, y + 2, w, 18), fill))
        if rng:
            a, b = x(rng[0]), x(rng[1])
            parts.append('<line class="whisker" x1="{:.1f}" x2="{:.1f}" y1="{}" y2="{}"/>'.format(a, b, y + 11, y + 11))
            for xx in (a, b):
                parts.append('<line class="whisker" x1="{0:.1f}" x2="{0:.1f}" y1="{1}" y2="{2}"/>'.format(xx, y + 6, y + 16))
        end = max(x1, x(rng[1]) if rng else x1)
        parts.append('<text class="value" x="{:.1f}" y="{}">{}</text>'.format(end + 6, y + 16, esc(unit_fmt(value))))
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


def _rounded_bar(x, y, w, h, r=4) -> str:
    r = min(r, w / 2, h / 2)
    return ("M{x0:.1f},{y0:.1f} H{x1:.1f} Q{x2:.1f},{y0:.1f} {x2:.1f},{y1:.1f} V{y2:.1f} "
            "Q{x2:.1f},{y3:.1f} {x1:.1f},{y3:.1f} H{x0:.1f} Z").format(
        x0=x, x1=x + w - r, x2=x + w, y0=y, y1=y + r, y2=y + h - r, y3=y + h)


def _stacked_lw(machines: List[Machine], width=720) -> str:
    labels = [line[0] for line in scoring.LW100_MIX]
    row_h, top, left, right = 34, 8, 200, 70
    plot_w = width - left - right
    totals = [m.lw["seconds"] for m in machines if m.lw["seconds"]]
    vmax = (max(totals) * 1.05) if totals else 1
    height = top + row_h * len(machines) + 6
    parts = ['<svg class="chart" viewBox="0 0 {} {}" role="img">'.format(width, height)]
    parts.append('<line class="axis" x1="{0}" x2="{0}" y1="{1}" y2="{2}"/>'.format(left, top - 4, height - 4))
    for i, m in enumerate(machines):
        y = top + i * row_h
        parts.append('<text class="label" x="{}" y="{}" text-anchor="end">{}</text>'.format(
            left - 10, y + 15, esc(m.label + (" (est.)" if m.estimated else ""))))
        x = float(left)
        by_label = {line["label"]: line for line in m.lw["lines"]}
        for k, lab in enumerate(labels):
            line = by_label.get(lab)
            if not line or line["seconds"] <= 0:
                continue
            w = plot_w * line["seconds"] / vmax
            tip = "{} · {}: {:.2f} s ({:.0f} × {:.1f} ms)".format(m.label, lab, line["seconds"], line["count"], line["ms_each"])
            seg_w = max(0.5, w - 2)
            parts.append('<g class="hit" tabindex="0" data-tip="{}"><rect x="{:.1f}" y="{}" width="{:.1f}" height="20" '
                         'fill="var(--series-{})"{}/></g>'.format(esc(tip), x, y + 2, seg_w, k + 1,
                                                                ' opacity="0.55"' if m.estimated else ""))
            x += w
        if m.lw["seconds"] is None:
            parts.append('<text class="value" x="{:.1f}" y="{}">not available</text>'.format(x + 4, y + 17))
        else:
            label_txt = "{:.1f} s".format(m.lw["seconds"])
            if m.lw["missing"]:
                label_txt += " ({} of {} parts)".format(
                    len(m.lw["lines"]), len(m.lw["lines"]) + len(m.lw["missing"]))
            parts.append('<text class="value" x="{:.1f}" y="{}">{}</text>'.format(
                x + 4, y + 17, esc(label_txt)))
    parts.append("</svg>")
    legend = "".join('<span class="key"><i style="background:var(--series-{})"></i>{}</span>'.format(k + 1, esc(l))
                     for k, l in enumerate(labels))
    return '<div class="legend">{}</div>{}'.format(legend, "".join(parts))


def _heat_color(score: Optional[float]) -> str:
    if not score:
        return "var(--empty)"
    t = max(-1.0, min(1.0, math.log2(score / 1000.0) / 3.0))  # ±3 doublings saturates
    mid, pos, neg = (240, 239, 236), (37, 106, 191), (208, 59, 59)
    pole = pos if t > 0 else neg
    a = abs(t) ** 0.8
    rgb = tuple(int(round(mid[i] + (pole[i] - mid[i]) * a)) for i in range(3))
    return "rgb({},{},{})".format(*rgb)


def _ink_for(color: str) -> str:
    if not color.startswith("rgb("):
        return "var(--text-primary)"
    r, g, b = [int(v) / 255.0 for v in color[4:-1].split(",")]
    lum = 0.2126 * r ** 2.2 + 0.7152 * g ** 2.2 + 0.0722 * b ** 2.2
    return "#0b0b0b" if lum > 0.35 else "#ffffff"


def _heatmap(machines: List[Machine]) -> str:
    cats = list(CATEGORY_WEIGHTS) + ["network"]
    head = "".join('<th scope="col">{}</th>'.format(esc(m.label + (" (est.)" if m.estimated else ""))) for m in machines)
    rows = ['<tr><th></th>{}</tr>'.format(head)]
    marks = []
    for m in machines:
        color = _heat_color(m.shown["agentmark"])
        marks.append('<td class="cell strong" style="background:{};color:{}" tabindex="0" data-tip="{}">{}</td>'.format(
            color, _ink_for(color), esc("{} AgentMark {}".format(m.label, fmt_num(m.shown["agentmark"]))),
            fmt_num(m.shown["agentmark"])))
    rows.append('<tr><th scope="row">AgentMark</th>{}</tr>'.format("".join(marks)))
    for cat in cats:
        cells = []
        for m in machines:
            v = m.shown["categories"].get(cat)
            color = _heat_color(v)
            tip = "{} · {}: {}".format(m.label, CATEGORY_TITLES[cat], fmt_num(v) if v else "not measured")
            cells.append('<td class="cell" style="background:{};color:{}" tabindex="0" data-tip="{}">{}</td>'.format(
                color, _ink_for(color), esc(tip), fmt_num(v) if v else "–"))
        weight = " <span class='muted small'>w{:.0f}</span>".format(CATEGORY_WEIGHTS[cat]) if cat in CATEGORY_WEIGHTS else ""
        rows.append('<tr><th scope="row">{}{}</th>{}</tr>'.format(esc(CATEGORY_TITLES[cat]), weight, "".join(cells)))
    scale = ('<div class="scale"><span>slower</span><i style="background:linear-gradient(90deg,{},{},{})"></i>'
             '<span>faster than reference</span></div>').format(_heat_color(125), _heat_color(1000), _heat_color(8000))
    return '<table class="heat">{}</table>{}'.format("".join(rows), scale)


def _raw_table(machines: List[Machine], reference) -> str:
    ids: List[str] = []
    for m in [reference] + [mm.doc for mm in machines]:
        for bid in m["benchmarks"]:
            if bid not in ids:
                ids.append(bid)
    head = "".join("<th>{}</th>".format(esc(m.label + (" (est.)" if m.estimated else ""))) for m in machines)
    rows = ["<tr><th>Test</th><th>Unit</th>{}</tr>".format(head)]
    for bid in ids:
        entries = [m.doc["benchmarks"].get(bid) for m in machines]
        known = [e for e in entries if e]
        if not known:
            continue
        lower = known[0]["lower_is_better"]
        vals = [float(e["median"]) for e in known]
        best = min(vals) if lower else max(vals)
        cells = []
        for m, e in zip(machines, entries):
            if not e:
                cells.append('<td class="num muted">–</td>')
                continue
            cls = "num" + (" best" if float(e["median"]) == best and len(known) > 1 else "") + (" muted" if m.estimated else "")
            flag = " ⚠" if e.get("noisy") else ""
            cells.append('<td class="{}" title="{}">{}{}</td>'.format(cls, esc(e.get("title", "")), fmt_num(e["median"]), flag))
        title = known[0].get("title", bid)
        tag = "" if known[0].get("scored") else " <span class='muted small'>(not scored)</span>"
        rows.append('<tr><td><code>{}</code><div class="muted small">{}{}</div></td><td>{}</td>{}</tr>'.format(
            esc(bid), esc(title), tag, esc(known[0]["unit"]), "".join(cells)))
    return '<table class="raw">{}</table>'.format("".join(rows))


def _findings(machines: List[Machine]) -> str:
    out = []
    for m in machines:
        if m.estimated:
            notes = m.doc.get("estimate_notes") or []
            items = "".join("<li>{}</li>".format(esc(n)) for n in notes)
            out.append('<div class="card"><h4>{} <span class="badge">estimate</span></h4><ul>{}</ul></div>'.format(
                esc(m.label), items))
            continue
        tips = advise(m.doc)
        icon = {"warn": "▲", "tip": "●", "info": "○"}
        items = "".join('<li><span class="lvl {0}">{1} {0}</span> <b>{2}.</b> {3}</li>'.format(
            esc(t["level"]), icon[t["level"]], esc(t["topic"]), esc(t["text"])) for t in tips) or "<li>No issues found.</li>"
        out.append('<div class="card"><h4>{}</h4><p class="muted small">{}</p><ul>{}</ul></div>'.format(
            esc(m.label), esc(m.system_line), items))
    return '<div class="cards">{}</div>'.format("".join(out))


def _live_section(paths) -> str:
    """Stacked bars of startup / local tool time / model time for each live run."""
    runs = []
    for path in paths or []:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        for r in doc.get("runs", []):
            runs.append(dict(r, machine=doc.get("label", "?")))
    if not runs:
        return ""
    runs.sort(key=lambda r: (r["machine"], r["harness"], r["task"]))
    width, row_h, top, left, right = 720, 26, 6, 250, 70
    plot_w = width - left - right
    vmax = max(r["wall_s"] for r in runs) * 1.05 or 1
    height = top + row_h * len(runs) + 6
    parts = ['<svg class="chart" viewBox="0 0 {} {}" role="img">'.format(width, height)]
    parts.append('<line class="axis" x1="{0}" x2="{0}" y1="{1}" y2="{2}"/>'.format(left, top - 2, height - 4))
    for i, r in enumerate(runs):
        y = top + i * row_h
        name = "{} · {} · {}".format(r["machine"], r["harness"], r["task"])
        parts.append('<text class="label small" x="{}" y="{}" text-anchor="end">{}{}</text>'.format(
            left - 10, y + 14, esc(name), "" if r["success"] else " ✗"))
        x = float(left)
        for key, fill, title in (("startup_s", "var(--series-3)", "start-up"), ("tool_s", "var(--series-1)", "local tools"),
                                 ("model_s", "var(--muted)", "model + network")):
            w = plot_w * float(r.get(key) or 0) / vmax
            if w <= 0:
                continue
            tip = "{} · {}: {:.1f} s of {:.1f} s".format(name, title, float(r.get(key) or 0), r["wall_s"])
            parts.append('<g class="hit" tabindex="0" data-tip="{}"><rect x="{:.1f}" y="{}" width="{:.1f}" height="16" '
                         'fill="{}"/></g>'.format(esc(tip), x, y + 2, max(0.5, w - 2), fill))
            x += w
        parts.append('<text class="value" x="{:.1f}" y="{}">{:.1f} s</text>'.format(x + 4, y + 14, r["wall_s"]))
    parts.append("</svg>")
    legend = ('<div class="legend"><span class="key"><i style="background:var(--series-3)"></i>Start-up</span>'
              '<span class="key"><i style="background:var(--series-1)"></i>Local tool time (this machine)</span>'
              '<span class="key"><i style="background:var(--muted)"></i>Model + network</span>'
              '<span class="muted">✗ = task failed verification</span></div>')
    rows = ["<tr><th>Machine</th><th>Harness</th><th>Task</th><th>Result</th><th>Wall s</th><th>Tools s</th>"
            "<th>Model s</th><th>Tool calls</th><th>Model</th></tr>"]
    for r in runs:
        rows.append("<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td class='num'>{:.1f}</td><td class='num'>{:.1f}</td>"
                    "<td class='num'>{:.1f}</td><td class='num'>{}</td><td class='muted'>{}</td></tr>".format(
                        esc(r["machine"]), esc(r["harness"]), esc(r["task"]),
                        "pass" if r["success"] else "fail: " + esc((r.get("verify_detail") or "")[:80]),
                        r["wall_s"], r["tool_s"], r["model_s"], r["tool_calls"], esc(r.get("model") or "")))
    return ('<h2>Live agent runs</h2><p class="lede">Real Claude Code and Codex sessions on three fixed tasks. '
            'Local tool time is what the machine controls; the rest is the model and the network.</p>'
            '<section>{}{}<details><summary>Run details</summary><table class="raw">{}</table></details></section>'
            ).format(legend, "".join(parts), "".join(rows))


# ---------------------------------------------------------------- page

def write_html(results, reference, path: str, estimates: Optional[str] = None, title: Optional[str] = None,
               live=None, category_weights=None) -> None:
    common = scoring.common_benchmarks(results + [reference])
    machines = [Machine(r, reference, common, category_weights=category_weights) for r in results]
    for docs in _estimate_docs(estimates, reference):
        machines.append(Machine(docs["mid"], reference, common, True, docs["pessimistic"],
                                docs["optimistic"], category_weights=category_weights))
    # Rank and display on the common comparable tests only: unrestricted
    # scores over different test subsets can reverse the true order.
    for m in machines:
        m.shown = m.common
        if not m.estimated and m.common.get("agentmark_ci"):
            m.range = tuple(m.common["agentmark_ci"])
    machines.sort(key=lambda m: -(m.shown["agentmark"] or 0))

    tiles = []
    for m in machines:
        rng = m.range
        rng_txt = "{}–{}".format(fmt_num(rng[0]), fmt_num(rng[1])) if rng else ""
        kind = "modelled range" if m.estimated else "90% " + m.shown.get("ci_kind", "within-run interval")
        lw_txt = "{:.1f} s".format(m.lw["seconds"]) if m.lw["seconds"] is not None else "not available"
        warn_txt = ('<div class="muted small">Comparability: {}</div>'.format(esc("; ".join(m.warnings)))
                    if m.warnings else "")
        tiles.append('<div class="tile{}"><div class="tile-label">{}{}</div><div class="tile-value">{}</div>'
                     '<div class="muted small">{} {}</div><div class="muted small">on {} common tests · coverage {:.0%}</div>'
                     '<div class="muted small">LW100 {} · Reach {}</div>{}</div>'.format(
                         " est" if m.estimated else "", esc(m.label),
                         ' <span class="badge">estimate</span>' if m.estimated else "",
                         fmt_num(m.shown["agentmark"]), kind, rng_txt, len(common), m.score["coverage"],
                         lw_txt, fmt_num(m.shown.get("reach")), warn_txt))

    am_rows = [(m.label, m.shown["agentmark"], m.short_system, m.estimated, m.range,
                "{}: AgentMark {} on common tests ({}; full-set {}, coverage {:.0%})".format(
                    m.label, fmt_num(m.shown["agentmark"]),
                    "estimate" if m.estimated else "measured",
                    fmt_num(m.score["agentmark"]), m.score["coverage"])) for m in machines]
    am_chart = _bar_chart(am_rows, fmt_num, ref_value=1000, ref_label="reference = 1000")

    transfer_blocks = []
    for bid, name in TRANSFER:
        rows = []
        for m in machines:
            e = m.doc["benchmarks"].get(bid)
            v = float(e["median"]) if e else None
            rows.append((m.label, v, "", m.estimated, None, "{} · {}: {} MB/s".format(m.label, name, fmt_num(v) if v else "–")))
        if any(r[1] for r in rows):
            transfer_blocks.append('<div class="mini"><h4>{} <span class="muted small">MB/s, log scale</span></h4>{}</div>'.format(
                esc(name), _bar_chart(rows, lambda v: fmt_num(v) + " MB/s", width=560, log=True)))

    compat = "".join(
        '<p class="lede"><b>Comparability ({}):</b> {}</p>'.format(esc(m.label), esc(w))
        for m in machines for w in m.warnings)
    compat += ('<p class="lede">Scores above use the {} scored tests every machine ran. '
               'Coverage says how much of the reference weight each machine scored.</p>').format(len(common))
    data_json = json.dumps({"reference": reference["label"],
                            "machines": [{"label": m.label, "estimated": m.estimated,
                                          "agentmark": m.shown["agentmark"], "reach": m.shown.get("reach"),
                                          "categories": m.shown["categories"], "lw100": m.lw["seconds"],
                                          "coverage": m.score["coverage"], "warnings": m.warnings}
                                         for m in machines]}, indent=1)
    page = _TEMPLATE.format(
        title=esc(title or "agentperf: how well each machine serves coding agents"),
        version=esc(PACKAGE_VERSION), ref=esc(reference["label"]), compat=compat,
        date=esc(datetime.date.today().isoformat()), tiles="".join(tiles), am_chart=am_chart,
        heat=_heatmap(machines), lw=_stacked_lw(machines), transfer="".join(transfer_blocks),
        findings=_findings(machines), raw=_raw_table(machines, reference), live=_live_section(live),
        series_light="".join("--series-{}:{};".format(i + 1, c) for i, c in enumerate(SERIES)),
        series_dark="".join("--series-{}:{};".format(i + 1, c) for i, c in enumerate(SERIES_DARK)),
        data=(data_json.replace("&", "\\u0026").replace("<", "\\u003c")
              .replace(">", "\\u003e")))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
.viz-root {{ color-scheme: light; --surface-1:#fcfcfb; --page:#f9f9f7; --text-primary:#0b0b0b; --text-secondary:#52514e;
  --muted:#898781; --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10); --empty:#f0efec; {series_light} }}
@media (prefers-color-scheme: dark) {{ :root:where(:not([data-theme="light"])) .viz-root {{ color-scheme: dark;
  --surface-1:#1a1a19; --page:#0d0d0d; --text-primary:#f0efec; --text-secondary:#c3c2b7; --grid:#2c2c2a; --axis:#383835;
  --border:rgba(255,255,255,0.10); --empty:#383835; {series_dark} }} }}
:root[data-theme="dark"] .viz-root {{ color-scheme: dark; --surface-1:#1a1a19; --page:#0d0d0d; --text-primary:#f0efec;
  --text-secondary:#c3c2b7; --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10); --empty:#383835; {series_dark} }}
body {{ margin:0; }}
.viz-root {{ background:var(--page); color:var(--text-primary); font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
  padding:28px; min-height:100vh; box-sizing:border-box; }}
main {{ max-width:1080px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; font-weight:600; }} h2 {{ font-size:16px; margin:34px 0 4px; font-weight:600; }}
h4 {{ margin:0 0 6px; font-size:14px; font-weight:600; }}
p.lede {{ color:var(--text-secondary); margin:0 0 18px; max-width:760px; }}
.muted {{ color:var(--muted); }} .small {{ font-size:12px; }}
section, .card {{ background:var(--surface-1); border:1px solid var(--border); border-radius:10px; padding:16px 18px; }}
section {{ margin-top:10px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:10px; margin-top:14px; }}
.tile {{ background:var(--surface-1); border:1px solid var(--border); border-radius:10px; padding:12px 14px; }}
.tile.est {{ border-style:dashed; }}
.tile-label {{ color:var(--text-secondary); font-size:13px; }} .tile-value {{ font-size:30px; font-weight:600; margin:2px 0; }}
.badge {{ font-size:11px; border:1px solid var(--axis); border-radius:999px; padding:0 6px; color:var(--text-secondary); font-weight:500; }}
svg.chart {{ width:100%; height:auto; display:block; }}
svg .axis {{ stroke:var(--axis); stroke-width:1; }} svg .refline {{ stroke:var(--text-secondary); stroke-width:1; }}
svg .label {{ fill:var(--text-primary); font-size:13px; }} svg .value {{ fill:var(--text-secondary); font-size:12px; font-variant-numeric:tabular-nums; }}
svg text.muted {{ fill:var(--muted); }} svg text.small {{ font-size:11px; }}
svg .whisker {{ stroke:var(--text-secondary); stroke-width:1.5; }}
svg .hit {{ cursor:default; outline:none; }} svg .hit:hover .bar, svg .hit:focus .bar, svg .hit:hover rect[fill^="var"] {{ filter:brightness(1.12); }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 14px; margin:4px 0 8px; color:var(--text-secondary); font-size:12px; }}
.key i {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }}
table {{ border-collapse:separate; border-spacing:2px; width:100%; }}
table.heat th {{ font-weight:500; color:var(--text-secondary); font-size:12px; text-align:left; padding:4px 8px; }}
table.heat td.cell {{ text-align:center; padding:8px 4px; border-radius:4px; font-variant-numeric:tabular-nums; font-size:13px; }}
table.heat td.strong {{ font-weight:600; }}
.scale {{ display:flex; align-items:center; gap:8px; margin-top:8px; font-size:12px; color:var(--muted); }}
.scale i {{ display:inline-block; width:180px; height:8px; border-radius:4px; }}
.minis {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(440px,1fr)); gap:18px; }}
table.raw {{ border-spacing:0; font-size:12.5px; }} table.raw th {{ text-align:right; color:var(--text-secondary); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--grid); }}
table.raw th:first-child, table.raw td:first-child {{ text-align:left; }} table.raw td {{ padding:5px 8px; border-bottom:1px solid var(--grid); vertical-align:top; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }} td.best {{ font-weight:650; }}
code {{ font-size:12px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:10px; }}
.cards ul {{ margin:6px 0 0; padding-left:18px; }} .cards li {{ margin:4px 0; }}
.lvl {{ font-size:11px; text-transform:uppercase; letter-spacing:.03em; color:var(--text-secondary); }}
.lvl.warn {{ color:#d03b3b; }}
#tip {{ position:fixed; pointer-events:none; background:var(--surface-1); color:var(--text-primary); border:1px solid var(--border);
  border-radius:6px; padding:6px 9px; font-size:12px; box-shadow:0 4px 14px rgba(0,0,0,.12); display:none; max-width:320px; z-index:10; }}
details summary {{ cursor:pointer; color:var(--text-secondary); }}
@media (max-width:560px) {{ .viz-root {{ padding:14px; }} section, .card {{ padding:12px; }}
  h1 {{ font-size:19px; }} .tiles {{ grid-template-columns:repeat(auto-fill,minmax(130px,1fr)); }}
  .tile-value {{ font-size:24px; }} .minis, .cards {{ grid-template-columns:1fr; }}
  .facts {{ columns:1; }} table.heat, table.raw, table.attr {{ display:block; overflow-x:auto; white-space:nowrap; }}
  .scale i {{ width:100px; }} }}
</style></head>
<body><div class="viz-root"><main>
<svg width="0" height="0" style="position:absolute"><defs><pattern id="hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
<rect width="6" height="6" fill="var(--series-1)" opacity="0.35"/><line x1="0" y1="0" x2="0" y2="6" stroke="var(--series-1)" stroke-width="2.5"/></pattern></defs></svg>
<h1>{title}</h1>
<p class="lede">AgentMark scores the local work an AI coding agent waits on: shell starts, file reads and edits, search, git, builds, and file transfer.
1000 = the reference machine (<b>{ref}</b>); 2000 means twice as fast. Network is scored separately as Reach.
LW100 is the modelled local wait per 100 agent tool calls. Hatched bars and dashed tiles are estimates, not measurements.
<span class="muted">agentperf {version} · {date}</span></p>
{compat}
<div class="tiles">{tiles}</div>

<h2>AgentMark</h2><section>{am_chart}</section>

<h2>Where each machine is fast or slow</h2>
<section>{heat}</section>

<h2>Local wait per 100 tool calls (LW100)</h2>
<p class="lede">Modelled from 6,416 real Claude Code tool calls: 79% Bash, 9% Read, 3% Write/Edit; 7.5 per 100 calls are folder-tree greps. Uses only tests every machine ran. Shorter is better.</p>
<section>{lw}</section>

<h2>File transfer</h2><section class="minis">{transfer}</section>

{live}

<h2>Findings</h2>{findings}

<h2>All measurements</h2>
<section><details><summary>Medians for every test (bold = best; ⚠ = varied by more than 15%)</summary>{raw}</details></section>
<script type="application/json" id="agentperf-data">{data}</script>
</main><div id="tip" role="tooltip"></div></div>
<script>
(function () {{
  var tip = document.getElementById('tip');
  function show(el, x, y) {{ tip.textContent = el.getAttribute('data-tip'); tip.style.display = 'block';
    tip.style.left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8) + 'px'; tip.style.top = (y + 14) + 'px'; }}
  document.querySelectorAll('[data-tip]').forEach(function (el) {{
    el.addEventListener('pointermove', function (e) {{ show(el, e.clientX, e.clientY); }});
    el.addEventListener('pointerleave', function () {{ tip.style.display = 'none'; }});
    el.addEventListener('focus', function () {{ var r = el.getBoundingClientRect(); show(el, r.left, r.bottom); }});
    el.addEventListener('blur', function () {{ tip.style.display = 'none'; }});
  }});
}})();
</script></body></html>
"""


# ---------------------------------------------------------------- single-machine report

def _css() -> str:
    """Reuse the compare template stylesheet (one source of truth)."""
    start = _TEMPLATE.index("<style>") + len("<style>")
    end = _TEMPLATE.index("</style>")
    css = _TEMPLATE[start:end]
    css = css.replace("{series_light}", "".join(
        "--series-{}:{};".format(i + 1, c) for i, c in enumerate(SERIES)))
    css = css.replace("{series_dark}", "".join(
        "--series-{}:{};".format(i + 1, c) for i, c in enumerate(SERIES_DARK)))
    css = css.replace("{{", "{").replace("}}", "}")
    extra = """
.win { border-left:4px solid var(--series-1); }
.win .gain { font-size:20px; font-weight:650; }
.conf { font-size:11px; border:1px solid var(--axis); border-radius:999px; padding:0 7px; color:var(--text-secondary); }
.conf.observed { border-color:var(--series-3); } .conf.likely { border-color:var(--series-4); }
.ev { font-size:12.5px; color:var(--text-secondary); } .fix { font-size:13px; }
table.attr { border-spacing:0; font-size:12.5px; width:100%; }
table.attr th, table.attr td { padding:5px 8px; border-bottom:1px solid var(--grid); text-align:right; }
table.attr th:first-child, table.attr td:first-child { text-align:left; }
.ratiobar { display:inline-block; height:8px; border-radius:4px; vertical-align:middle; }
.facts { columns:2; font-size:13px; } .facts div { break-inside:avoid; margin:2px 0; }
"""
    return css + extra


_TOOLTIP_JS = """<script>
(function () {
  var tip = document.getElementById('tip');
  function show(el, x, y) { tip.textContent = el.getAttribute('data-tip'); tip.style.display = 'block';
    tip.style.left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8) + 'px'; tip.style.top = (y + 14) + 'px'; }
  document.querySelectorAll('[data-tip]').forEach(function (el) {
    el.addEventListener('pointermove', function (e) { show(el, e.clientX, e.clientY); });
    el.addEventListener('pointerleave', function () { tip.style.display = 'none'; });
    el.addEventListener('focus', function () { var r = el.getBoundingClientRect(); show(el, r.left, r.bottom); });
    el.addEventListener('blur', function () { tip.style.display = 'none'; });
  });
})();
</script>"""


def _cause_cards(result, reference) -> str:
    from . import diagnose
    items = diagnose.causes(result, reference)
    if not items:
        return ('<section><p>No dominant software cause stands out. The remaining gap to the '
                'reference is explained by the per-test attribution below (mostly hardware '
                'and OS process/file costs).</p></section>')
    major, minor = diagnose.partition_causes(items)
    cards = []
    for i, c in enumerate(major, 1):
        if c.get("gain_estimated") and c["gain_points"]:
            gain = ('<div class="gain">up to +{} AgentMark</div>'
                    '<div class="muted small">if the affected tests reached reference speed '
                    '(upper bound; gains overlap and do not add up)</div>').format(
                        fmt_gain(c["gain_points"]))
        else:
            gain = '<div class="muted small">Gain not estimated.</div>'
        ev = "".join('<li class="ev">{}</li>'.format(esc(e)) for e in c["evidence"])
        fix = "".join('<li class="fix">{}</li>'.format(esc(f)) for f in c["fix"])
        aff = c.get("affected_text") or ", ".join(c["affected"]) or "not tied to scored tests"
        cards.append('<div class="card win"><h4>{}. {} <span class="conf {}">{}</span></h4>{}'
                     '<p>{}</p><p class="muted small">Evidence</p><ul>{}</ul>'
                     '<p class="muted small">Fix</p><ul>{}</ul>'
                     '<p class="muted small">Affected tests: {}</p></div>'.format(
                         i, esc(c["topic"]), esc(c["confidence"]), esc(c["confidence"]),
                         gain, esc(c["summary"]), ev, fix, esc(aff)))
    out = ""
    if cards:
        out += '<div class="cards">{}</div>'.format("".join(cards))
    else:
        out += ('<section><p>No large win stands out. The minor items below are each worth '
                'under +{} AgentMark.</p></section>').format(fmt_gain(diagnose.MINOR_GAIN_POINTS))
    if minor:
        rows = "".join('<li><b>{}</b> <span class="conf {}">{}</span> - up to +{} AgentMark. {}</li>'.format(
            esc(c["topic"]), esc(c["confidence"]), esc(c["confidence"]),
            fmt_gain(c["gain_points"]), esc(c["fix"][0]) if c["fix"] else "") for c in minor)
        out += ('<section><details><summary>Minor wins ({} item(s), each under +{} AgentMark)</summary>'
                '<ul>{}</ul></details></section>').format(len(minor), fmt_gain(diagnose.MINOR_GAIN_POINTS), rows)
    return out


def _attribution_sections(result, reference) -> str:
    from . import diagnose
    attr = diagnose.attribution(result, reference)
    order = list(CATEGORY_WEIGHTS) + ["network"]
    out = []
    for cat in order:
        block = attr["categories"].get(cat)
        if not block:
            continue
        rows = ["<tr><th>Test</th><th>You</th><th>Reference</th><th>Unit</th>"
                "<th>Speed vs ref</th><th>Score</th><th>AgentMark cost</th></tr>"]
        for t in block["tests"]:
            pct = min(100.0, t["ratio"] * 50.0)  # 2x reference fills the bar
            color = "var(--series-1)" if t["ratio"] >= 1 else "var(--series-2)"
            tip = esc("{}: {} vs {} {} (x{})".format(
                t["id"], fmt_num(t["value"]), fmt_num(t["reference"]), t["unit"], fmt_ratio(t["ratio"])))
            cost = "-" + fmt_gain(t["gain_points"]) if t["gain_points"] and round(t["gain_points"]) >= 1 else "-"
            rows.append('<tr tabindex="0" data-tip="{}"><td><code>{}</code><div class="muted small">{}</div></td>'
                        '<td class="num">{}</td><td class="num">{}</td><td>{}</td>'
                        '<td class="num"><span class="ratiobar" style="width:{:.0f}px;background:{}"></span> x{}</td>'
                        '<td class="num">{}</td><td class="num">{}</td></tr>'.format(
                            tip, esc(t["id"]), esc(t["title"]), fmt_num(t["value"]),
                            fmt_num(t["reference"]), esc(t["unit"]), pct, color, fmt_ratio(t["ratio"]),
                            fmt_num(t["score"]), cost))
        head = '{} - score {}'.format(esc(block["title"]), fmt_num(block["score"]))
        if block["weight"]:
            head += ' <span class="muted small">weight {:.0f} of 100</span>'.format(block["weight"])
        if block["gain_points"]:
            head += (' <span class="muted small">matching the reference here is worth up to '
                     '+{} AgentMark</span>').format(fmt_gain(block["gain_points"]))
        out.append('<h3>{}</h3><section><table class="attr">{}</table></section>'.format(
            head, "".join(rows)))
    return "".join(out)


def _system_facts(result) -> str:
    s = result.get("system", {})
    labels = [
        ("cpu_model", "CPU"), ("os_version", "OS"), ("logical_cpus", "Logical CPUs"),
        ("physical_cpus", "Physical CPUs"), ("ram_gb", "RAM (GB)"), ("fs_type", "Filesystem"),
        ("python", "Python"), ("power_source", "Power"), ("power_plan", "Power plan"),
        ("governor", "CPU governor"), ("defender_realtime", "Defender real-time"),
        ("dev_drive", "Dev Drive"), ("rotational", "Spinning disk"),
        ("raid_resync", "RAID resync"), ("workdir_on_windows_drive", "Workdir on /mnt"),
        ("cwd_on_windows_drive", "Ran from /mnt"),
        ("workdir_in_icloud", "Workdir synced"), ("spotlight", "Spotlight"),
        ("windows_search_running", "Windows Search"), ("time_machine_running", "Time Machine"),
        ("disk_free_gb", "Disk free (GB)"), ("virtualization", "Virtualization"),
    ]
    divs = []
    for key, name in labels:
        value = s.get(key)
        if value not in (None, "", []):
            divs.append("<div><b>{}:</b> {}</div>".format(esc(name), esc(value)))
    agents = s.get("security_agents") or []
    if agents:
        divs.append("<div><b>Security software:</b> {}</div>".format(esc(", ".join(agents))))
    anti = [a for a in (s.get("antivirus") or []) if a.get("active")]
    if anti:
        divs.append("<div><b>Active antivirus:</b> {}</div>".format(
            esc(", ".join(str(a.get("name")) for a in anti))))
    top = s.get("top_processes") or []
    if top:
        divs.append("<div><b>Busiest at start:</b> {}</div>".format(
            esc(", ".join("{} {}%".format(p.get("name"), p.get("cpu")) for p in top[:3]))))
    elif s.get("processes_over_80pct"):
        divs.append("<div><b>Busy processes over 80%:</b> {}</div>".format(s["processes_over_80pct"]))
    return '<div class="facts">{}</div>'.format("".join(divs) or "No system facts recorded.")


def write_report(result, reference, path: str, references=None, title=None) -> None:
    """Single-machine explanatory report: score, placement, wins, attribution."""
    from . import diagnose
    common = scoring.common_benchmarks([result, reference])
    machine = Machine(result, reference, common)
    sc = machine.score
    place = diagnose.placement(result, reference, references or [])
    def short(label):
        return label if len(label) <= 30 else label[:29] + "..."

    # Placement scores are restricted to the tests every profile shares,
    # so the "you" bar uses the same basis as the profile bars.
    rows = [(short(result["label"] + " (you)"), place["agentmark"], machine.short_system, False,
             tuple(sc["agentmark_ci"]) if sc.get("agentmark_ci") else None,
             "Your machine: AgentMark {} on the shared tests ({} on all your tests)".format(
                 fmt_num(place["agentmark"]), fmt_num(sc["agentmark"])))]
    for r in place["references"]:
        if r["label"] == result["label"]:
            continue  # the example case: reporting on a bundled profile itself
        rows.append((short(r["label"]), r["agentmark"], "bundled reference", False, None,
                     "{}: AgentMark {}".format(r["label"], fmt_num(r["agentmark"]))))
    rows.sort(key=lambda r: -(r[1] or 0))
    place_chart = _bar_chart(rows, fmt_num, ref_value=1000, ref_label="reference = 1000")
    ci = sc.get("agentmark_ci") or [None, None]
    my_lw, ref_lw = scoring.lw100_common(result, reference)
    lw_value = "{:.1f} s".format(my_lw["seconds"]) if my_lw["seconds"] is not None else "not available"
    lw_sub = ("estimated; reference {:.1f} s on the same parts".format(ref_lw["seconds"])
              if ref_lw["seconds"] is not None else "estimated local wait per 100 tool calls")
    if my_lw["missing"]:
        lw_sub += "; covers {} of {} parts".format(
            len(my_lw["lines"]), len(my_lw["lines"]) + len(my_lw["missing"]))
    tiles = (
        '<div class="tile"><div class="tile-label">AgentMark</div><div class="tile-value">{}</div>'
        '<div class="muted small">90% {} {}-{}</div></div>'
        '<div class="tile"><div class="tile-label">LW100 (local wait / 100 calls)</div>'
        '<div class="tile-value">{}</div><div class="muted small">{}</div></div>'
        '<div class="tile"><div class="tile-label">Reach (network)</div><div class="tile-value">{}</div>'
        '<div class="muted small">model APIs and CDNs, scored apart</div></div>'
        '<div class="tile"><div class="tile-label">Coverage</div><div class="tile-value">{:.0%}</div>'
        '<div class="muted small">share of reference weight scored</div></div>').format(
            fmt_num(sc["agentmark"]), esc(sc.get("ci_kind", "within-run interval")),
            fmt_num(ci[0]), fmt_num(ci[1]),
            esc(lw_value), esc(lw_sub),
            fmt_num(sc.get("reach")), sc["coverage"])
    drags = diagnose.attribution(result, reference)["biggest_drags"]
    drag_items = "".join('<li><code>{}</code> x{} vs reference - worth up to +{} AgentMark</li>'.format(
        esc(t["id"]), fmt_ratio(t["ratio"]), fmt_gain(t["gain_points"])) for t in drags[:5]) or "<li>None: no test trails the reference.</li>"
    comparable = esc("suite {} - schema {} - corpus {} - reference '{}'".format(
        result.get("suite_version", SUITE_VERSION), result.get("schema", "?"),
        (result.get("corpus") or {}).get("fixture_hash", "?"), reference["label"]))
    body = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title><style>%%CSS%%</style></head>
<body><div class="viz-root"><main>
<h1>%%TITLE%%</h1>
<p class="lede">%%VERDICT%% 1000 = the reference (<b>%%REF%%</b>); 2000 means twice as fast.
Causes are labelled <b>observed</b> (a probe shows it), <b>likely</b> (probe plus matching pattern),
or <b>possible</b> (pattern only). Gains are upper bounds and overlap.
<span class="muted">agentperf %%VERSION%% - %%DATE%% - %%COMPARABLE%%</span></p>
%%COMPAT%%
<div class="tiles">%%TILES%%</div>
<h2>Where you stand</h2><section>%%PLACEMENT%%<p class="muted small">%%PLACENOTE%%</p></section>
<h2>Biggest wins: likely causes and fixes</h2>%%WINS%%
<h2>Biggest single drags</h2><section><ul>%%DRAGS%%</ul></section>
<h2>Why, category by category</h2>%%ATTR%%
<h2>Local wait per 100 tool calls (LW100)</h2><section>%%LW%%</section>
<h2>What the probes found on this machine</h2><section>%%FACTS%%</section>
<h2>Findings checklist</h2>%%FINDINGS%%
<h2>All measurements</h2><section><details><summary>Medians for every test</summary>%%RAW%%</details></section>
<p class="muted small">How to read this: AgentMark is a weighted geometric mean over fixed tests.
Single runs vary about 10-15%; use --runs 3 for close calls. Scores compare only when the corpus
fixture hash, the scored benchmark definitions, the measurement methods, and the measurement/scoring
fingerprint all match, against the same reference file. Share an anonymized result with: agentperf share result.json</p>
</main><div id="tip" role="tooltip"></div></div>
%%JS%%</body></html>"""
    page = (body.replace("%%TITLE%%", esc(title or "agentperf report: {}".format(result["label"])))
            .replace("%%CSS%%", _css())
            .replace("%%VERDICT%%", esc(diagnose.verdict(
                sc["agentmark"], (result.get("system") or {}).get("os_family"))))
            .replace("%%REF%%", esc(reference["label"]))
            .replace("%%VERSION%%", esc(PACKAGE_VERSION))
            .replace("%%DATE%%", esc(datetime.date.today().isoformat()))
            .replace("%%COMPARABLE%%", comparable)
            .replace("%%COMPAT%%", "".join(
                '<p class="lede"><b>Comparability:</b> {}</p>'.format(esc(w))
                for w in scoring.compatibility_warnings(result, reference)))
            .replace("%%TILES%%", tiles)
            .replace("%%PLACEMENT%%", place_chart)
            .replace("%%PLACENOTE%%", esc(place["note"]) +
                     " Faster than {} of {} bundled profiles.".format(place["faster_than"], place["of"]))
            .replace("%%WINS%%", _cause_cards(result, reference))
            .replace("%%DRAGS%%", drag_items)
            .replace("%%ATTR%%", _attribution_sections(result, reference))
            .replace("%%LW%%", _stacked_lw([machine,
                                             Machine(reference, reference, common)]))
            .replace("%%FACTS%%", _system_facts(result))
            .replace("%%FINDINGS%%", _findings([machine]))
            .replace("%%RAW%%", _raw_table([machine], reference))
            .replace("%%JS%%", _TOOLTIP_JS))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
