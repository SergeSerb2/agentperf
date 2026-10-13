"""Command line: agentperf run | compare | report | reference | remote | list | live."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import List, Optional

from . import PACKAGE_VERSION, SUITE_VERSION, scoring

REF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "references")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _split(text: Optional[str]) -> Optional[List[str]]:
    return [p.strip() for p in text.split(",") if p.strip()] if text else None


def _read_bundled(name: str) -> Optional[bytes]:
    """Read a file from agentperf/references - works on disk and inside a .pyz."""
    path = os.path.join(REF_DIR, name)
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        pass
    try:
        import pkgutil
        return pkgutil.get_data("agentperf", "references/" + name)
    except (OSError, FileNotFoundError):
        return None


def _reference_path(name_or_path: Optional[str]) -> Optional[str]:
    """A path, a bundled reference name, or None for the default reference."""
    if name_or_path and os.path.exists(name_or_path):
        return name_or_path
    name = name_or_path or _default_reference_name()
    if not name:
        return None
    path = os.path.join(REF_DIR, name + ".json")
    return path if os.path.exists(path) else None


def _default_reference_name() -> Optional[str]:
    data = _read_bundled("DEFAULT")
    return data.decode("utf-8").strip() or None if data else None


def _load_reference(arg: Optional[str]):
    if arg and os.path.exists(arg):
        return scoring.load(arg)
    name = arg or _default_reference_name()
    if not name:
        return None
    path = _reference_path(name)
    if path:
        return scoring.load(path)
    data = _read_bundled(name + ".json")
    return json.loads(data.decode("utf-8")) if data else None


def _write_json(doc, path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=False)
        fh.write("\n")


def _say(message: str = "") -> None:
    """Print, but never fail: a dropped SSH session must not stop a run before it saves."""
    try:
        print(message, flush=True)
    except (OSError, ValueError):
        pass


def _all_bundled_references():
    docs = []
    data = _read_bundled("index.json")
    if not data:
        return docs
    index = json.loads(data.decode("utf-8"))
    for profile in index.get("profiles", []):
        raw = _read_bundled(profile["file"])
        if raw:
            docs.append(json.loads(raw.decode("utf-8")))
    return docs


def cmd_run(args) -> int:
    from .report_text import run_report
    from .runner import default_workdir, run_suite
    if getattr(args, "quick", False):
        args.profile = "quick"
    workdir = os.path.abspath(os.path.expanduser(args.workdir or default_workdir()))
    exclude = _split(args.exclude) or []
    if args.no_network:
        exclude.append("network")
    docs = []
    for i in range(args.runs):
        if args.runs > 1:
            _say("\n=== run {} of {}".format(i + 1, args.runs))
        docs.append(run_suite(workdir, profile=args.profile, label=args.label, only=_split(args.only),
                              exclude=exclude, fetch=args.fetch_tools and i == 0,
                              clean=args.clean and i == args.runs - 1, log=_say))
    result = combine_runs(docs) if len(docs) > 1 else docs[0]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(result["label"]))
    out = args.out or "agentperf-{}-{}.json".format(safe_label.strip("-") or "result", stamp)
    _write_json(result, out)
    _say("")
    reference = _load_reference(args.ref)
    _say(run_report(result, reference))
    _say("\nSaved {}".format(os.path.abspath(out)))
    html_path = getattr(args, "html", None)
    if html_path and reference:
        from .report_html import write_report
        write_report(result, reference, html_path, references=_all_bundled_references())
        _say("Report {}".format(os.path.abspath(html_path)))
    return 0


def cmd_report(args) -> int:
    result = scoring.load(args.result)
    reference = _load_reference(args.ref)
    if reference is None:
        print("No reference found. Pass --ref with a reference name or result JSON.")
        return 2
    from .report_text import run_report
    print(run_report(result, reference))
    html_path = args.html or (os.path.splitext(args.result)[0] + "-report.html")
    from .report_html import write_report
    write_report(result, reference, html_path, references=_all_bundled_references())
    print("\nWrote {}".format(os.path.abspath(html_path)))
    return 0


def cmd_share(args) -> int:
    from .privacy import anonymize
    doc = scoring.load(args.result)
    clean, changes = anonymize(doc, label=args.label)
    out = args.out or (os.path.splitext(args.result)[0] + "-share.json")
    _write_json(clean, out)
    print("Wrote anonymized result {}".format(os.path.abspath(out)))
    print("Removed or generalized:")
    for change in changes:
        print(" - {}".format(change))
    print("Kept: all measurements and generic system facts (CPU, OS, filesystem, flags).")
    print("Check the file before you post it in public. See SECURITY.md.")
    return 0


def cmd_references(args) -> int:
    reference = _load_reference(None)
    print("Bundled reference profiles (anonymized; anchor scores 1000):")
    for doc in _all_bundled_references():
        meta = doc.get("reference_profile", {})
        mark = scoring.score(doc, reference)["agentmark"] if reference else None
        from .util import fmt_num
        print(" - {:<22} {:>6}  {} ({})".format(
            meta.get("name", doc["label"]), fmt_num(mark), doc["label"], meta.get("measured", "?")))
        if meta.get("description"):
            print("   {}".format(meta["description"]))
    print("Default: {}".format(_default_reference_name()))
    print("Use one with: agentperf compare results/*.json --ref <name>")
    return 0


def _tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if home and path.startswith(home) else path


def cmd_doctor(args) -> int:
    """Fast pre-flight: tools, workdir, and the probes that affect scores. No benchmarks."""
    from . import env, tools
    from .runner import MIN_FREE_GB, default_workdir
    from .util import os_family
    workdir = os.path.abspath(os.path.expanduser(args.workdir or default_workdir()))
    os.makedirs(workdir, exist_ok=True)
    print("agentperf {} (suite {}) on {} (Python {})".format(
        PACKAGE_VERSION, SUITE_VERSION, os_family(), sys.version.split()[0]))
    found = tools.find_tools()
    for name in sorted(found):
        shown = _tilde(found[name]) if found[name] else "NOT FOUND (related tests will skip)"
        print("  tool {:<10} {}".format(name, shown))
    system = env.collect(workdir)
    for key in ("cpu_model", "os_version", "logical_cpus", "ram_gb", "fs_type",
                "disk_free_gb", "power_source", "power_plan", "governor",
                "defender_realtime", "dev_drive", "rotational", "raid_resync",
                "workdir_on_windows_drive", "workdir_in_icloud"):
        if system.get(key) not in (None, "", [], False):
            print("  {:<26} {}".format(key, system[key]))
    if system.get("security_agents"):
        print("  {:<26} {}".format("security_agents", ", ".join(system["security_agents"])))
    if system.get("load_avg_start"):
        print("  {:<26} {}".format("load_avg", system["load_avg_start"]))
    print("Workdir: {} (scores measure this disk; put repos on the same disk)".format(_tilde(workdir)))
    checks = []
    free = system.get("disk_free_gb")
    if free is not None and free < MIN_FREE_GB:
        checks.append("only {:.1f} GB free on the work disk; a run needs {:.1f} GB and will stop".format(
            free, MIN_FREE_GB))
    if not found.get("git"):
        checks.append("git not found: all git tests will skip and coverage will drop")
    else:
        from .util import run_text
        if not run_text([found["git"], "--version"], timeout=15):
            checks.append("git is present but did not answer --version; on macOS without "
                          "the Command Line Tools the /usr/bin/git shim can stall on a GUI "
                          "prompt (fix: xcode-select --install). Git tests will skip.")
    if not found.get("rg"):
        checks.append("ripgrep not found: rg tests will skip (or run with --fetch-tools)")
    if system.get("workdir_on_windows_drive"):
        checks.append("workdir is under /mnt/ in WSL: scores would measure NTFS through 9P; "
                      "move it to the Linux filesystem")
    if str(system.get("fs_type") or "").lower() in ("tmpfs", "ramfs", "overlay"):
        checks.append("workdir filesystem is {}: disk tests would measure RAM, not storage".format(
            system.get("fs_type")))
    load = system.get("load_avg_start")
    cpus = system.get("logical_cpus") or 1
    if load and load[0] > 0.5 * cpus:
        checks.append("system is busy (load {:.1f} on {} CPUs): close heavy apps before a run "
                      "you plan to compare".format(load[0], cpus))
    if system.get("power_source") == "battery":
        checks.append("running on battery: plug in power for stable numbers")
    if checks:
        print("Checks:")
        for check in checks:
            print("  - " + check)
    else:
        print("Checks: no problems found.")
    print("Next: agentperf run --quick --no-network   (fast smoke run)")
    return 0


def cmd_compare(args) -> int:
    from .report_text import compare_report
    results = [scoring.load(p) for p in args.results]
    reference = _load_reference(args.ref)
    if reference is None:
        reference = results[0]
        print("No reference given; using {} as the reference.\n".format(reference["label"]))
    weights = scoring.equal_category_weights() if getattr(args, "weights", "default") == "equal" else None
    print(compare_report(results, reference, category_weights=weights))
    if args.html:
        from .report_html import write_html
        write_html(results, reference, args.html, estimates=args.estimates, live=args.live,
                   category_weights=weights)
        print("\nWrote {}".format(os.path.abspath(args.html)))
    return 0


def combine_runs(docs):
    """Median of per-run medians for each benchmark, with all samples pooled.
    Several runs make a steadier reference than one."""
    from .util import median, summarize
    def machine(doc):
        system = doc.get("system") or {}
        return (system.get("cpu_model"), system.get("os_family"))
    if len({machine(d) for d in docs}) > 1:
        raise ValueError("these results do not look like one machine (CPU model or OS "
                         "family differ: {}); refusing to combine them".format(
                             ", ".join(str(machine(d)) for d in docs)))
    combined = json.loads(json.dumps(docs[0]))
    for bid, entry in combined["benchmarks"].items():
        runs = [d["benchmarks"][bid] for d in docs if bid in d["benchmarks"]]
        pooled = [s for r in runs for s in r["samples"]]
        stats = summarize(pooled)
        stats["median"] = median([float(r["median"]) for r in runs])
        entry.update(stats)
        entry["samples"] = pooled
        entry["runs"] = len(runs)
        entry["noisy"] = stats["cv"] > 0.15
    combined["combined_runs"] = [{"run_id": d.get("run_id"), "started_at": d.get("started_at")} for d in docs]
    return combined


def cmd_reference(args) -> int:
    docs = [scoring.load(p) for p in args.result]
    doc = combine_runs(docs) if len(docs) > 1 else docs[0]
    name = args.name or doc["label"]
    if not os.path.isdir(os.path.dirname(REF_DIR)) or not os.access(os.path.dirname(REF_DIR), os.W_OK):
        print("Cannot store a bundled reference: the agentperf package directory "
              "({}) is not writable (single-file .pyz or system install). "
              "Use `agentperf combine --out my-ref.json` and pass it with "
              "--ref instead.".format(os.path.dirname(REF_DIR)))
        return 2
    os.makedirs(REF_DIR, exist_ok=True)
    _write_json(doc, os.path.join(REF_DIR, name + ".json"))
    if args.default:
        with open(os.path.join(REF_DIR, "DEFAULT"), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
    print("Saved reference '{}' from {} run(s){}".format(name, len(docs), " (default)" if args.default else ""))
    return 0


def cmd_merge(args) -> int:
    """Combine result files of one machine (for example a separate network pass)."""
    docs = [scoring.load(p) for p in args.results]
    merged = docs[0]
    for doc in docs[1:]:
        merged["benchmarks"].update(doc["benchmarks"])
        for key in doc["benchmarks"]:
            merged.get("skipped", {}).pop(key, None)
        for key, why in doc.get("skipped", {}).items():
            if key not in merged["benchmarks"]:
                merged.setdefault("skipped", {})[key] = why
        merged.setdefault("merged_runs", []).append(
            {"run_id": doc.get("run_id"), "started_at": doc.get("started_at"),
             "benchmarks": sorted(doc["benchmarks"])})
    _write_json(merged, args.out)
    print("Wrote {} ({} benchmarks)".format(args.out, len(merged["benchmarks"])))
    return 0


def cmd_combine(args) -> int:
    doc = combine_runs([scoring.load(p) for p in args.results])
    if args.label:
        doc["label"] = args.label
    _write_json(doc, args.out)
    print("Wrote {} (median of {} runs)".format(args.out, len(args.results)))
    return 0


def cmd_list(args) -> int:
    from . import benches  # noqa: F401
    from .registry import CATEGORY_WEIGHTS, REGISTRY
    for b in REGISTRY:
        tag = "scored" if b.scored and b.category in CATEGORY_WEIGHTS else (
            "reach" if b.category == "network" else "diag")
        print("{:<24} {:<7} {:<6} {}".format(b.id, tag, b.unit, b.title))
    return 0


def cmd_remote(args) -> int:
    from .remote import run_remote
    return run_remote(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentperf",
                                description="Measure how well this computer serves AI coding agents.")
    p.add_argument("--version", action="version",
                   version="agentperf {} (suite {})".format(PACKAGE_VERSION, SUITE_VERSION))
    sub = p.add_subparsers(dest="command")

    r = sub.add_parser("run", help="run the system suite on this machine")
    r.add_argument("--profile", choices=["quick", "standard"], default="standard")
    r.add_argument("--quick", action="store_true", help="same as --profile quick (fewer repetitions)")
    r.add_argument("--html", help="also write the explanatory HTML report to this path")
    r.add_argument("--label", help="name for this machine in reports (default: hostname)")
    r.add_argument("--workdir", help="work directory on the disk to test (default ~/.agentperf/work)")
    r.add_argument("--out", help="result JSON path")
    r.add_argument("--only", help="comma list of benchmark ids or categories")
    r.add_argument("--exclude", help="comma list of benchmark ids or categories")
    r.add_argument("--no-network", action="store_true", help="skip the network (Reach) tests")
    r.add_argument("--fetch-tools", action="store_true",
                   help="download the pinned ripgrep (and Node if missing) first")
    r.add_argument("--clean", action="store_true", help="delete the work directory afterwards")
    r.add_argument("--ref", help="reference name or result JSON to score against")
    r.add_argument("--runs", type=_positive_int, default=1,
                   help="repeat the suite and keep the median of the run medians (single runs vary ~10-15%%)")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="compare result files against a reference")
    c.add_argument("results", nargs="+")
    c.add_argument("--ref", help="reference name or result JSON (default: bundled default)")
    c.add_argument("--html", help="also write a self-contained HTML report")
    c.add_argument("--weights", choices=["default", "equal"], default="default",
                   help="category weights: default, or equal for a sensitivity check")
    c.add_argument("--estimates", help="JSON file of modelled (not measured) systems for the HTML report")
    c.add_argument("--live", nargs="*", default=[], help="live-run JSON files to add to the HTML report")
    c.set_defaults(func=cmd_compare)

    f = sub.add_parser("reference", help="store result files of one machine as a named reference profile")
    f.add_argument("result", nargs="+", help="one or more result files; several runs are combined")
    f.add_argument("--name")
    f.add_argument("--default", action="store_true")
    f.set_defaults(func=cmd_reference)

    mg = sub.add_parser("merge", help="merge result files from separate passes on one machine")
    mg.add_argument("results", nargs="+")
    mg.add_argument("--out", required=True)
    mg.set_defaults(func=cmd_merge)

    cb = sub.add_parser("combine", help="combine repeated runs of one machine (median of medians)")
    cb.add_argument("results", nargs="+")
    cb.add_argument("--out", required=True)
    cb.add_argument("--label")
    cb.set_defaults(func=cmd_combine)

    ls = sub.add_parser("list", help="list benchmarks")
    ls.set_defaults(func=cmd_list)

    rp = sub.add_parser("report", help="explain one result: verdict, causes, fixes (writes HTML)")
    rp.add_argument("result")
    rp.add_argument("--ref", help="reference name or result JSON (default: bundled default)")
    rp.add_argument("--html", help="HTML report path (default: <result>-report.html)")
    rp.set_defaults(func=cmd_report)

    sh = sub.add_parser("share", help="write an anonymized result that is safe to post in public")
    sh.add_argument("result")
    sh.add_argument("--out", help="output path (default: <result>-share.json)")
    sh.add_argument("--label", help="generic label for the shared result")
    sh.set_defaults(func=cmd_share)

    rf = sub.add_parser("references", help="list the bundled anonymized reference profiles")
    rf.set_defaults(func=cmd_references)

    dc = sub.add_parser("doctor", help="fast pre-flight check (tools, disk, probes); runs no benchmarks")
    dc.add_argument("--workdir", help="work directory to probe (default ~/.agentperf/work)")
    dc.set_defaults(func=cmd_doctor)

    rm = sub.add_parser("remote", help="run the suite on another machine over SSH",
                        epilog="Arguments after -- are passed to the remote command.")
    rm.add_argument("host", help="ssh host alias")
    rm.add_argument("--wsl", metavar="DISTRO", help="run inside this WSL distro on a Windows host")
    rm.add_argument("--python", help="remote Python command (default: python3, or python on Windows)")
    rm.add_argument("--label")
    rm.add_argument("--profile", choices=["quick", "standard"], default="standard")
    rm.add_argument("--fetch-tools", action="store_true")
    rm.add_argument("--clean", action="store_true")
    rm.add_argument("--no-network", action="store_true")
    rm.add_argument("--only")
    rm.add_argument("--out-dir", default="results")
    rm.add_argument("--mode", choices=["run", "live"], default="run",
                    help="run the system suite (default) or live agent sessions")
    rm.set_defaults(func=cmd_remote, extra=[])

    try:
        from .live import add_parser as add_live_parser
        add_live_parser(sub)
    except Exception:  # live mode is optional; never let it break the core CLI
        pass
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: List[str] = []
    if argv and argv[0] == "remote" and "--" in argv:  # arguments for the remote command
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    args = parser.parse_args(argv)
    if extra:
        args.extra = extra
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return int(args.func(args) or 0)
    except FileNotFoundError as exc:
        print("error: no such file: {}".format(exc.filename or exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        # Expected user-facing failures: unreadable or malformed result files,
        # unwritable output paths, mismatched inputs. Bugs still traceback.
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Partial work files stay under the work directory; "
              "rerun when ready.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
