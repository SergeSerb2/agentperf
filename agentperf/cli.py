"""Command line: run, compare, report, doctor, remote, skill, and friends.

Exit codes are stable across versions (see docs/result-schema.md):
0 = success, 1 = the command ran but the work failed or was refused
(empty run, remote failure, skill conflict, live task failure),
2 = usage or bad input (unknown flags, missing or malformed files,
incompatible results), 130 = interrupted with Ctrl-C.
``doctor`` exits 0 when the check itself ran; read its ``ok`` field
with ``--json`` for machine problems.
"""

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


def _bundled_names() -> List[str]:
    data = _read_bundled("index.json")
    if not data:
        return []
    try:
        return [p.get("name", "") for p in json.loads(data.decode("utf-8")).get("profiles", [])]
    except ValueError:
        return []


def _load_reference(arg: Optional[str]):
    """Load a reference. An explicitly named reference that does not exist
    is an error; silently falling back would change the scoring baseline."""
    if arg and os.path.exists(arg):
        return scoring.load(arg)
    name = arg or _default_reference_name()
    if not name:
        return None
    path = _reference_path(name)
    if path:
        return scoring.load(path)
    data = _read_bundled(name + ".json")
    if data:
        return scoring.validate_result(json.loads(data.decode("utf-8")),
                                       "bundled reference " + name)
    if arg:
        raise ValueError("no such reference: {!r} (not a file, and not a bundled "
                         "profile{}). Run `agentperf references` to list them.".format(
                             arg, ": " + ", ".join(_bundled_names()) if _bundled_names() else ""))
    return None


def _write_json(doc, path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        # Strict: a non-finite measurement must fail loudly, never land
        # in a result file as a bare NaN/Infinity token.
        json.dump(doc, fh, indent=1, sort_keys=False, allow_nan=False)
        fh.write("\n")


def _say(message: str = "") -> None:
    """Print, but never fail: a dropped SSH session must not stop a run before it saves."""
    try:
        print(message, flush=True)
    except (OSError, ValueError):
        pass


def _say_err(message: str = "") -> None:
    """Progress on stderr, for runs whose stdout carries a JSON summary."""
    try:
        print(message, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def _add_json(parser) -> None:
    parser.add_argument("--json", nargs="?", const="-", default=None, metavar="PATH",
                        help="write a machine-readable summary as JSON: to PATH, "
                             "or to stdout with '-' or no value (progress then goes "
                             "to stderr). Put --json after file arguments, or use "
                             "--json=PATH, so a file name is not taken as PATH.")


def _all_bundled_references():
    docs = []
    data = _read_bundled("index.json")
    if not data:
        return docs
    index = json.loads(data.decode("utf-8"))
    for profile in index.get("profiles", []):
        raw = _read_bundled(profile["file"])
        if raw:
            docs.append(scoring.validate_result(
                json.loads(raw.decode("utf-8")),
                "bundled reference " + profile["file"]))
    return docs


def cmd_run(args) -> int:
    from .report_text import run_report
    from .runner import default_workdir, run_suite
    if getattr(args, "quick", False):
        args.profile = "quick"
    json_stdout = getattr(args, "json", None) == "-"
    say = _say_err if json_stdout else _say
    # Resolve the scoring baseline before measuring anything: a missing
    # bundled default must fail clearly up front, never turn the run's
    # own result into a silent self-reference afterwards. Measuring
    # without scoring is an explicit opt-in (--no-reference).
    no_reference = getattr(args, "no_reference", False)
    if no_reference and args.ref:
        raise ValueError("--ref and --no-reference cannot be used together")
    if no_reference and getattr(args, "html", None):
        raise ValueError("--html needs a reference; drop --no-reference or --html")
    reference = None if no_reference else _load_reference(args.ref)
    if reference is None and not no_reference:
        raise ValueError(
            "no reference found: the bundled default reference is missing "
            "(broken install?). Pass --ref with a reference name or result "
            "JSON, or --no-reference to measure without scoring.")
    workdir = os.path.abspath(os.path.expanduser(args.workdir or default_workdir()))
    exclude = _split(args.exclude) or []
    if args.no_network:
        exclude.append("network")
    docs = []
    for i in range(args.runs):
        if args.runs > 1:
            say("\n=== run {} of {}".format(i + 1, args.runs))
        docs.append(run_suite(workdir, profile=args.profile, label=args.label, only=_split(args.only),
                              exclude=exclude, fetch=args.fetch_tools and i == 0,
                              clean=args.clean and i == args.runs - 1, log=say))
    result = combine_runs(docs) if len(docs) > 1 else docs[0]
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c if c.isalnum() or c in "._-" else "-" for c in str(result["label"]))
    out = args.out or "agentperf-{}-{}.json".format(safe_label.strip("-") or "result", stamp)
    _write_json(result, out)
    html_path = getattr(args, "html", None)
    written_html = None
    if html_path and reference:
        from .report_html import write_report
        write_report(result, reference, html_path, references=_all_bundled_references())
        written_html = html_path
    if not json_stdout:
        _say("")
        _say(run_report(result, reference))
        _say("\nSaved {}".format(os.path.abspath(out)))
        if written_html:
            _say("Report {}".format(os.path.abspath(written_html)))
    if getattr(args, "json", None):
        from .summary import emit, result_summary
        doc = result_summary(result, reference, result_path=out, html_path=written_html)
        doc["command"] = "run"
        emit(doc, args.json, say=_say_err if json_stdout else _say)
    return 0


def cmd_report(args) -> int:
    result = scoring.load(args.result)
    reference = _load_reference(args.ref)
    if reference is None:
        raise ValueError("no reference found. Pass --ref with a reference "
                         "name or result JSON.")
    json_stdout = getattr(args, "json", None) == "-"
    html_path = args.html or (os.path.splitext(args.result)[0] + "-report.html")
    from .report_html import write_report
    write_report(result, reference, html_path, references=_all_bundled_references())
    if not json_stdout:
        from .report_text import run_report
        print(run_report(result, reference))
        print("\nWrote {}".format(os.path.abspath(html_path)))
    if getattr(args, "json", None):
        from .summary import emit, result_summary
        doc = result_summary(result, reference, result_path=args.result, html_path=html_path)
        doc["command"] = "report"
        emit(doc, args.json, say=_say_err if json_stdout else print)
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
    from .runner import MIN_FREE_GB, default_workdir, workdir_owned
    from .util import os_family
    workdir = os.path.abspath(os.path.expanduser(args.workdir or default_workdir()))
    # Doctor creates nothing: probe the nearest existing ancestor instead.
    probe_dir = workdir
    while not os.path.isdir(probe_dir):
        parent = os.path.dirname(probe_dir)
        if parent == probe_dir:
            break
        probe_dir = parent
    found = tools.find_tools()
    system = env.collect(probe_dir)
    probe_keys = ("cpu_model", "os_version", "logical_cpus", "ram_gb", "fs_type",
                  "disk_free_gb", "power_source", "power_plan", "governor",
                  "low_power_mode",
                  "defender_realtime", "dev_drive", "rotational", "raid_resync",
                  "workdir_on_windows_drive", "workdir_in_icloud")
    checks = []
    free = system.get("disk_free_gb")
    if free is not None and free < MIN_FREE_GB:
        checks.append("only {:.1f} GB free on the work disk; a run needs {:.1f} GB and will stop".format(
            free, MIN_FREE_GB))
    if os.path.isdir(workdir) and os.listdir(workdir) and not workdir_owned(workdir):
        checks.append("workdir {} is not empty and was not created by agentperf; a run "
                      "will refuse it (it deletes its own corpus/repo/scratch inside). "
                      "Pick an empty directory or the default.".format(_tilde(workdir)))
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
    if system.get("low_power_mode"):
        checks.append("Low Power Mode is on: it slows the CPU; turn it off "
                      "for a comparable run")
    json_stdout = getattr(args, "json", None) == "-"
    if not json_stdout:
        print("agentperf {} (suite {}) on {} (Python {})".format(
            PACKAGE_VERSION, SUITE_VERSION, os_family(), sys.version.split()[0]))
        for name in sorted(found):
            shown = _tilde(found[name]) if found[name] else "NOT FOUND (related tests will skip)"
            print("  tool {:<10} {}".format(name, shown))
        for key in probe_keys:
            if system.get(key) not in (None, "", [], False):
                print("  {:<26} {}".format(key, system[key]))
        if system.get("security_agents"):
            print("  {:<26} {}".format("security_agents", ", ".join(system["security_agents"])))
        if system.get("load_avg_start"):
            print("  {:<26} {}".format("load_avg", system["load_avg_start"]))
        print("Workdir: {} (scores measure this disk; put repos on the same disk)".format(_tilde(workdir)))
        if checks:
            print("Checks:")
            for check in checks:
                print("  - " + check)
        else:
            print("Checks: no problems found.")
        print("Next: agentperf run --quick --no-network   (fast smoke run)")
    if getattr(args, "json", None):
        from .summary import SUMMARY_VERSION, emit
        emit({
            "command": "doctor",
            "summary_version": SUMMARY_VERSION,
            "package_version": PACKAGE_VERSION,
            "suite_version": SUITE_VERSION,
            "os_family": os_family(),
            "python": sys.version.split()[0],
            "workdir": _tilde(workdir),
            # Doctor exits 0 when the check itself ran; agents must read
            # `ok` and `checks`, not the exit code, for machine problems.
            "ok": not checks,
            "checks": checks,
            "tools": {k: (_tilde(v) if v else None) for k, v in sorted(found.items())},
            "missing_tools": sorted(k for k, v in found.items() if not v),
            "system": {k: system.get(k) for k in probe_keys
                       if system.get(k) not in (None, "", [], False)},
            "next": "agentperf run --quick --no-network",
        }, args.json, say=_say_err if json_stdout else print)
    return 0


def cmd_compare(args) -> int:
    from .report_text import compare_report
    results = [scoring.load(p) for p in args.results]
    reference = _load_reference(args.ref)
    json_stdout = getattr(args, "json", None) == "-"
    if reference is None:
        if not getattr(args, "self_reference", False):
            raise ValueError(
                "no reference found: the bundled default reference is "
                "missing (broken install?). Pass --ref with a reference "
                "name or result JSON, or --self-reference to score the "
                "first result against itself.")
        reference = results[0]
        if not json_stdout:
            print("Using {} as its own reference (--self-reference).\n".format(
                reference["label"]))
    weights = scoring.equal_category_weights() if getattr(args, "weights", "default") == "equal" else None
    if args.html:
        from .report_html import write_html
        write_html(results, reference, args.html, estimates=args.estimates, live=args.live,
                   category_weights=weights)
    if not json_stdout:
        print(compare_report(results, reference, category_weights=weights))
        if args.html:
            print("\nWrote {}".format(os.path.abspath(args.html)))
    if getattr(args, "json", None):
        from .summary import compare_summary, emit
        emit(compare_summary(results, reference, category_weights=weights,
                             html_path=args.html),
             args.json, say=_say_err if json_stdout else print)
    return 0


def _check_combinable(docs, what: str) -> None:
    """Refuse to mix results that did not measure the same things.

    Checks machine identity, schema, corpus fixture, fingerprint, and —
    for benchmarks present in more than one file — definition, unit, and
    measurement method. Without this, a combined file can hide a mixture
    that later comparability checks cannot detect."""
    # Each input's stored fingerprint must match its own contents
    # first: combining must never launder a corrupt or edited file
    # into a fresh-looking valid one.
    for doc in docs:
        stored = doc.get("fingerprint")
        if stored and doc.get("benchmarks"):
            try:
                recomputed = scoring.fingerprint(doc)
            except Exception:
                recomputed = None
            if recomputed is not None and stored != recomputed:
                raise ValueError(
                    "an input's stored fingerprint ({}) does not match its "
                    "contents ({}): the file was edited or written under a "
                    "different scoring config; refusing to {} it".format(
                        stored, recomputed, what))
    def machine(doc):
        system = doc.get("system") or {}
        return (system.get("cpu_model"), system.get("os_family"))
    if len({machine(d) for d in docs}) > 1:
        raise ValueError("these results do not look like one machine (CPU model or OS "
                         "family differ: {}); refusing to {} them".format(
                             ", ".join(str(machine(d)) for d in docs), what))
    def distinct(values):
        seen = []
        for v in values:
            if v is not None and v not in seen:
                seen.append(v)
        return seen
    # Fingerprints are not compared whole here: a separate network pass
    # legitimately contains a subset of benchmarks. The per-benchmark
    # signature check below covers shared tests.
    checks = [
        ("schema", distinct([d.get("schema") for d in docs])),
        ("corpus fixture hash", distinct([(d.get("corpus") or {}).get("fixture_hash") for d in docs])),
        # The harness interpreter and the scored shell are part of what
        # was measured: the same CPU under a different Python or shell
        # version is a different measurement environment.
        ("Python version", distinct([(d.get("system") or {}).get("python") for d in docs])),
        ("shell version", distinct([
            (d.get("tool_versions") or {}).get("shell")
            or (d.get("tool_versions") or {}).get("bash") for d in docs])),
        ("recorded scoring config", distinct([
            repr(sorted(scoring._weights_for(d).items())) for d in docs])),
        ("shell environment policy", distinct([
            scoring.shell_env_policy(d) for d in docs])),
    ]
    for name, values in checks:
        if len(values) > 1:
            raise ValueError("these results have different {}s ({}); refusing to {} "
                             "incompatible measurements".format(
                                 name, ", ".join(str(v) for v in values), what))
    seen = {}
    for doc in docs:
        for bid, entry in (doc.get("benchmarks") or {}).items():
            sig = (scoring._definition(entry), entry.get("title"), scoring._method(entry))
            if bid in seen and seen[bid] != sig:
                raise ValueError("benchmark {} was measured differently across these "
                                 "files (definition or method differs); refusing to "
                                 "{} them".format(bid, what))
            seen[bid] = sig


def combine_runs(docs):
    """Median of per-run medians for each benchmark.

    Per-run samples are kept (``run_samples``) so scoring can bootstrap the
    same estimator. Several runs make a steadier reference than one."""
    from .util import median, summarize
    _check_combinable(docs, "combine")
    combined = json.loads(json.dumps(docs[0]))
    all_ids = list(combined["benchmarks"])
    for doc in docs[1:]:
        for bid in doc["benchmarks"]:
            if bid not in all_ids:
                all_ids.append(bid)
                combined["benchmarks"][bid] = json.loads(
                    json.dumps(doc["benchmarks"][bid]))
    for bid in all_ids:
        entry = combined["benchmarks"][bid]
        runs = [d["benchmarks"][bid] for d in docs if bid in d["benchmarks"]]
        pooled = [s for r in runs for s in r["samples"]]
        stats = summarize(pooled)
        stats["median"] = median([float(r["median"]) for r in runs])
        entry.update(stats)
        entry["samples"] = pooled
        # Aligned with `docs`: an empty slot is a run that did not
        # measure this benchmark. Scoring's bootstrap draws run
        # indices once across benchmarks, so the slots must line up.
        entry["run_samples"] = [
            list(d["benchmarks"][bid]["samples"]) if bid in d["benchmarks"] else []
            for d in docs]
        entry["run_medians"] = [float(r["median"]) for r in runs]
        entry["runs"] = len(runs)
        entry["noisy"] = stats["cv"] > 0.15
    combined["combined_runs"] = [{"run_id": d.get("run_id"), "started_at": d.get("started_at")} for d in docs]
    # Preserve the inputs' agreed recorded scoring config (combinability
    # already refused differing configs); never silently substitute
    # today's default weights.
    combined["scoring_config"] = scoring.scoring_config_snapshot(
        scoring._weights_for(docs[0]))
    combined["fingerprint"] = scoring.fingerprint(combined)
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
    doc = json.loads(json.dumps(doc))
    doc["scoring_config"] = scoring.scoring_config_snapshot(scoring._weights_for(doc))
    doc["fingerprint"] = scoring.fingerprint(doc)
    _write_json(doc, os.path.join(REF_DIR, name + ".json"))
    if args.default:
        with open(os.path.join(REF_DIR, "DEFAULT"), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
    print("Saved reference '{}' from {} run(s){}".format(name, len(docs), " (default)" if args.default else ""))
    return 0


def cmd_merge(args) -> int:
    """Combine result files of one machine (for example a separate network pass)."""
    docs = [scoring.load(p) for p in args.results]
    _check_combinable(docs, "merge")
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
    # The merged document measures benchmarks no single input had
    # together; keep its recorded config and fingerprint truthful.
    merged["scoring_config"] = scoring.scoring_config_snapshot(
        scoring._weights_for(merged))
    merged["fingerprint"] = scoring.fingerprint(merged)
    _write_json(merged, args.out)
    print("Wrote {} ({} benchmarks)".format(args.out, len(merged["benchmarks"])))
    return 0


def cmd_combine(args) -> int:
    doc = combine_runs([scoring.load(p) for p in args.results])
    if args.label:
        doc["label"] = args.label
    _write_json(doc, args.out)
    print("Wrote {} (per-test median of {} runs)".format(args.out, len(args.results)))
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
    code, summary = run_remote(args)
    json_stdout = getattr(args, "json", None) == "-"
    say = _say_err if json_stdout else print
    compare_path = getattr(args, "compare", None)
    if compare_path:
        fetched = summary.get("results") or []
        if not fetched:
            print("error: --compare needs at least one fetched result, but no run "
                  "produced one.", file=sys.stderr)
            code = code or 1
        else:
            results = [scoring.load(p) for p in fetched]  # type: ignore[union-attr]
            reference = _load_reference(getattr(args, "ref", None))
            if reference is None and getattr(args, "self_reference", False):
                reference = results[0]
            if reference is None:
                summary["compare_error"] = (
                    "no reference found for --compare; pass --ref or "
                    "--self-reference")
                print("error: " + summary["compare_error"], file=sys.stderr)  # type: ignore[operator]
                code = code or 1
                if getattr(args, "json", None):
                    from .summary import emit
                    emit(summary, args.json, say=say)
                return code
            from .report_html import write_html
            from .report_text import compare_report
            write_html(results, reference, compare_path)
            summary["compare_html"] = os.path.abspath(compare_path)
            from .summary import compare_summary
            cmp_doc = compare_summary(results, reference, html_path=compare_path)
            summary["comparison"] = cmp_doc
            if not json_stdout:
                print("\n" + compare_report(results, reference))
                print("\nWrote {}".format(os.path.abspath(compare_path)))
    if getattr(args, "json", None):
        from .summary import emit
        emit(summary, args.json, say=say)
    return code


def _skill_project_dir(args) -> Optional[str]:
    value = getattr(args, "project", None)
    if value is None or value is False:
        return None
    return os.path.abspath(os.path.expanduser(value if value is not True else "."))


def cmd_skill_install(args) -> int:
    from . import skill as skill_mod
    from .summary import SUMMARY_VERSION, emit
    project = _skill_project_dir(args)
    targets = skill_mod.resolve_targets(getattr(args, "target", None), project)
    entries = []
    json_stdout = getattr(args, "json", None) == "-"
    for target in targets:
        dest = skill_mod.target_path(target, project=project)
        status, message = skill_mod.install_one(dest, force=args.force)
        entries.append({"target": target, "path": dest, "status": status,
                        "message": message})
        if not json_stdout:
            print("{}: {} - {}".format(target, status, message))
            print("  {}".format(dest))
    if getattr(args, "json", None):
        emit({"command": "skill install", "summary_version": SUMMARY_VERSION,
              "package_version": PACKAGE_VERSION, "project": project,
              "targets": entries}, args.json,
             say=_say_err if json_stdout else print)
    return 1 if any(e["status"] in ("refused", "failed") for e in entries) else 0


def cmd_skill_uninstall(args) -> int:
    from . import skill as skill_mod
    from .summary import SUMMARY_VERSION, emit
    project = _skill_project_dir(args)
    targets = skill_mod.resolve_targets(getattr(args, "target", None), project)
    entries = []
    json_stdout = getattr(args, "json", None) == "-"
    for target in targets:
        dest = skill_mod.target_path(target, project=project)
        status, message = skill_mod.uninstall_one(dest, force=args.force)
        entries.append({"target": target, "path": dest, "status": status,
                        "message": message})
        if not json_stdout:
            print("{}: {} - {}".format(target, status, message))
            print("  {}".format(dest))
    if getattr(args, "json", None):
        emit({"command": "skill uninstall", "summary_version": SUMMARY_VERSION,
              "package_version": PACKAGE_VERSION, "project": project,
              "targets": entries}, args.json,
             say=_say_err if json_stdout else print)
    return 1 if any(e["status"] in ("refused", "failed") for e in entries) else 0


def cmd_skill_path(args) -> int:
    from . import skill as skill_mod
    from .summary import SUMMARY_VERSION, emit
    project = _skill_project_dir(args)
    requested = getattr(args, "target", None)
    json_stdout = getattr(args, "json", None) == "-"
    if requested or project:
        targets = skill_mod.resolve_targets(requested, project)
        entries = []
        for target in targets:
            dest = skill_mod.target_path(target, project=project)
            state, detail = skill_mod.inspect(dest)
            entries.append({"target": target, "path": dest, "state": state,
                            "installed_version": detail.get("installed_version")})
            if not json_stdout:
                print("{}: {} ({})".format(target, dest, state))
        if getattr(args, "json", None):
            emit({"command": "skill path", "summary_version": SUMMARY_VERSION,
                  "package_version": PACKAGE_VERSION, "project": project,
                  "bundled": skill_mod.bundled_location(),
                  "files": skill_mod.SKILL_FILES, "targets": entries}, args.json,
                 say=_say_err if json_stdout else print)
        return 0
    location = skill_mod.bundled_location()
    if not json_stdout:
        print(location)
        for rel in skill_mod.SKILL_FILES:
            print("  {}".format(rel))
    if getattr(args, "json", None):
        emit({"command": "skill path", "summary_version": SUMMARY_VERSION,
              "package_version": PACKAGE_VERSION, "bundled": location,
              "files": skill_mod.SKILL_FILES}, args.json,
             say=_say_err if json_stdout else print)
    return 0


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
    r.add_argument("--no-reference", action="store_true",
                   help="measure without scoring against a reference (the result "
                        "file is still written; no AgentMark is reported)")
    r.add_argument("--runs", type=_positive_int, default=1,
                   help="repeat the suite and keep, per test, the median of the run medians (single runs vary ~10-15%%)")
    _add_json(r)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="compare result files against a reference")
    c.add_argument("results", nargs="+")
    c.add_argument("--ref", help="reference name or result JSON (default: bundled default)")
    c.add_argument("--self-reference", action="store_true",
                   help="if no reference can be loaded, score the first result "
                        "against itself instead of failing")
    c.add_argument("--html", help="also write a self-contained HTML report")
    c.add_argument("--weights", choices=["default", "equal"], default="default",
                   help="category weights: default, or equal for a sensitivity check")
    c.add_argument("--estimates", help="JSON file of modelled (not measured) systems for the HTML report")
    c.add_argument("--live", nargs="*", default=[], help="live-run JSON files to add to the HTML report")
    _add_json(c)
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

    cb = sub.add_parser("combine", help="combine repeated runs of one machine (per-test median of run medians)")
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
    _add_json(rp)
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
    _add_json(dc)
    dc.set_defaults(func=cmd_doctor)

    rm = sub.add_parser(
        "remote", help="run the suite on other machines over SSH, one at a time",
        epilog="Hosts run strictly one after another, in the order given "
               "(never run native Windows beside its own WSL2: they share "
               "one PC). Arguments after -- go to the remote command. "
               "Examples:\n"
               "  agentperf remote my-mac linux-box --quick --fetch-tools\n"
               "  agentperf remote my-pc --wsl my-pc:Ubuntu --fetch-tools --compare fleet.html\n"
               "  agentperf remote my-pc --wsl Ubuntu   (single host, WSL only)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    rm.add_argument("hosts", nargs="+", metavar="HOST",
                    help="ssh host alias(es), run in order")
    rm.add_argument("--wsl", action="append", default=[], metavar="[HOST:]DISTRO",
                    help="add a WSL run: HOST:DISTRO (repeatable), or a bare DISTRO "
                         "when exactly one host is given")
    rm.add_argument("--python", help="remote Python command (default: python3, or python on Windows)")
    rm.add_argument("--label", help="result label (single run only; default: from the host name)")
    rm.add_argument("--profile", choices=["quick", "standard"], default="standard")
    rm.add_argument("--quick", action="store_true", help="same as --profile quick on every host")
    rm.add_argument("--runs", type=_positive_int, default=1,
                    help="repeat the suite on each host and keep, per test, the median of medians")
    rm.add_argument("--workdir", help="work directory on each host (default: ~/.agentperf/work there)")
    rm.add_argument("--fetch-tools", action="store_true")
    rm.add_argument("--clean", action="store_true")
    rm.add_argument("--no-network", action="store_true")
    rm.add_argument("--only")
    rm.add_argument("--out-dir", default="results")
    rm.add_argument("--ref", help="reference for --compare (default: bundled default)")
    rm.add_argument("--self-reference", action="store_true",
                    help="for --compare: if no reference can be loaded, score "
                         "the first fetched result against itself")
    rm.add_argument("--compare", metavar="FILE.html",
                    help="after the runs, write one comparison report of the fetched results")
    rm.add_argument("--mode", choices=["run", "live", "doctor"], default="run",
                    help="run the system suite (default), live agent sessions, or doctor")
    _add_json(rm)
    rm.set_defaults(func=cmd_remote, extra=[])

    sk = sub.add_parser("skill", help="install or locate the bundled Agent Skill for coding agents")
    sk_sub = sk.add_subparsers(dest="skill_command")
    for verb, fn, text in (
            ("install", cmd_skill_install,
             "copy the bundled skill into agent skill folders"),
            ("uninstall", cmd_skill_uninstall, "remove a skill copy agentperf installed"),
            ("path", cmd_skill_path, "show where the bundled or installed skill lives")):
        sp = sk_sub.add_parser(verb, help=text)
        sp.add_argument("--target", action="append", choices=["claude", "codex", "agents"],
                        help="skill folder to use (repeatable; default: all three for "
                             "user installs, claude+agents with --project)")
        sp.add_argument("--project", nargs="?", const=".", default=None, metavar="DIR",
                        help="use DIR/.claude/skills and DIR/.agents/skills "
                             "(default DIR: current directory) instead of the user folders")
        if verb != "path":
            sp.add_argument("--force", action="store_true",
                            help="replace or remove a copy that was edited (extra files are kept)")
        _add_json(sp)
        sp.set_defaults(func=fn)

    try:
        from .live import add_parser as add_live_parser
        add_live_parser(sub)
    except Exception:  # live mode is optional; never let it break the core CLI
        pass
    return p


def _prescan_json(argv) -> Optional[str]:
    """Best-effort --json destination before parsing, so even an
    argparse failure can honor the error-summary contract."""
    for i, token in enumerate(argv):
        if token == "--":
            break
        if token.startswith("--json="):
            return token.split("=", 1)[1] or "-"
        if token == "--json":
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            return nxt if nxt and not nxt.startswith("-") else "-"
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    extra: List[str] = []
    if argv and argv[0] == "remote" and "--" in argv:  # arguments for the remote command
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1:]
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = int(exc.code or 0) if isinstance(exc.code, int) else 2
        dest = _prescan_json(argv)
        if code and dest:
            try:
                from .summary import emit, error_summary
                emit(error_summary(argv[0] if argv else "agentperf",
                                   "invalid command line (see usage above)",
                                   code), dest, say=_say_err)
            except Exception:
                pass
        return code
    if extra:
        args.extra = extra
    if not getattr(args, "func", None):
        if argv and argv[0] == "skill":
            print("error: `agentperf skill` needs a subcommand: "
                  "install, path, or uninstall (try `agentperf skill install --help`)",
                  file=sys.stderr)
        else:
            parser.print_help()
        return 2
    json_dest = getattr(args, "json", None)
    command_name = getattr(args, "command", None) or "agentperf"
    if getattr(args, "skill_command", None):
        command_name = "skill " + args.skill_command

    def emit_error(message, code) -> None:
        # The --json contract holds on failure paths too: one JSON
        # object (the documented error summary), never empty stdout.
        if not json_dest:
            return
        try:
            from .summary import emit, error_summary
            emit(error_summary(command_name, str(message), code), json_dest,
                 say=_say_err)
        except Exception:
            pass

    try:
        return int(args.func(args) or 0)
    except SystemExit as exc:
        # Graceful refusals and run failures (workdir refused, empty run,
        # unreachable host) are exit 1 with one clear line, never a traceback.
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            if exc.code:
                emit_error("command failed with exit code {}".format(exc.code),
                           int(exc.code))
            return int(exc.code)
        print("error: {}".format(exc.code), file=sys.stderr)
        emit_error(exc.code, 1)
        return 1
    except FileNotFoundError as exc:
        message = "no such file: {}".format(exc.filename or exc)
        print("error: {}".format(message), file=sys.stderr)
        emit_error(message, 2)
        return 2
    except (OSError, ValueError, KeyError) as exc:
        # Expected user-facing failures: unreadable or malformed result files,
        # unwritable output paths, mismatched inputs. Bugs still traceback.
        print("error: {}".format(exc), file=sys.stderr)
        emit_error(exc, 2)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Partial work files stay under the work directory; "
              "rerun when ready.", file=sys.stderr)
        emit_error("interrupted", 130)
        return 130


if __name__ == "__main__":
    sys.exit(main())
