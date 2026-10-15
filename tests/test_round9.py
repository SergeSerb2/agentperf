"""Round-9 regression tests: blockers from the round-8 verification.

R1: share scrubs unknown children of the objects it keeps whole.
R2: combine and merge never relabel a shell benchmark's policy.
R3: results missing report fields are refused before any report runs.
Temp folders only: no network, no real home folders. Invented names only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFS = os.path.join(ROOT, "agentperf", "references")
PROFILES = ("wsl2-i7-12700k", "windows-i7-12700k", "macos-m1-pro",
            "macos-m5-pro")
SECRET = "/Users/Alice/Private Repo/file"


def _quiet_main(argv):
    from agentperf.cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _load(name):
    with open(os.path.join(REFS, name + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _entry(category, median):
    return {"category": category, "title": category, "unit": "ms",
            "lower_is_better": True, "weight": 1.0, "scored": True,
            "ops": 1, "median": median, "samples": [median], "cv": 0.0,
            "noisy": False, "meta": {}}


def _doc(benches, shell_launch):
    doc = {"label": "box", "profile": "standard", "benchmarks": benches,
           "skipped": {}, "schema": 1, "corpus": {"fixture_hash": "h1"},
           "system": {"cpu_model": "Test CPU", "os_family": "linux"}}
    if shell_launch is not None:
        doc["measurement_env"] = {"shell_launch": shell_launch}
    return doc


class ShareUnknownFieldsTest(unittest.TestCase):
    """R1: keeping corpus, measurement_env, and scoring_config must not
    keep text the tool never writes there."""

    def test_unknown_children_are_scrubbed_known_ones_kept(self):
        from agentperf import scoring
        from agentperf.privacy import anonymize
        for name in PROFILES:
            doc = _load(name)
            doc.setdefault("measurement_env", {})
            doc["scoring_config"] = scoring.scoring_config_snapshot(
                scoring._weights_for(doc))
            for key in ("corpus", "measurement_env", "scoring_config"):
                doc[key]["operator_note"] = SECRET
                doc[key]["nested"] = {"where": [SECRET]}
            before = {k: copy.deepcopy(doc[k]) for k in
                      ("corpus", "measurement_env", "scoring_config")}
            clean, _changes = anonymize(copy.deepcopy(doc))
            text = json.dumps(clean)
            self.assertNotIn("Alice", text, name)
            self.assertNotIn("Private Repo", text, name)
            for key, value in before.items():
                for child, kept in value.items():
                    if child in ("operator_note", "nested"):
                        continue
                    self.assertEqual(clean[key][child], kept, (name, key))
            mine = scoring.score(clean, _load(PROFILES[0]))
            theirs = scoring.score(doc, _load(PROFILES[0]))
            self.assertEqual(mine["agentmark"], theirs["agentmark"], name)
            self.assertEqual(scoring.shell_env_policies(clean),
                             scoring.shell_env_policies(doc), name)


class ShellPolicyProvenanceTest(unittest.TestCase):
    """R2: a benchmark taken from one input must not inherit another
    input's measurement_env fallback."""

    def _pair(self):
        a = _doc({"process.shell": _entry("process", 1.0)},
                 "sanitized (removed: BASH_ENV)")
        b = _doc({"process.parallel": _entry("process", 2.0)},
                 "inherited shell env")
        return a, b

    def test_combine_refuses_disjoint_differing_fallbacks(self):
        from agentperf.cli import combine_runs
        a, b = self._pair()
        with self.assertRaises(ValueError) as caught:
            combine_runs([a, b])
        self.assertIn("process.parallel", str(caught.exception))
        with self.assertRaises(ValueError):
            combine_runs([b, a])

    def test_unrecorded_policy_never_reads_as_sanitized(self):
        from agentperf.cli import combine_runs
        a, _b = self._pair()
        old = _doc({"process.parallel": _entry("process", 2.0)}, None)
        with self.assertRaises(ValueError):
            combine_runs([a, old])

    def test_merge_command_refuses_and_writes_nothing(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        a, b = self._pair()
        paths = []
        for i, doc in enumerate((a, b)):
            path = os.path.join(tmp, "{}.json".format(i))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            paths.append(path)
        out = os.path.join(tmp, "merged.json")
        for argv in (["merge"] + paths + ["--out", out],
                     ["combine"] + paths + ["--out", out]):
            code, _o, err = _quiet_main(argv)
            self.assertEqual(code, 2, (argv[0], err))
            self.assertNotIn("Traceback", err)
            self.assertFalse(os.path.exists(out), argv[0])

    def test_agreeing_or_per_benchmark_policies_still_merge(self):
        from agentperf import scoring
        from agentperf.cli import combine_runs
        a, b = self._pair()
        b["measurement_env"]["shell_launch"] = "sanitized (removed: BASH_ENV)"
        out = combine_runs([a, b])
        self.assertEqual(set(scoring.shell_env_policies(out).values()),
                         {"sanitized"})
        # A benchmark's own note travels with it, whatever the fallback.
        a, b = self._pair()
        b["benchmarks"]["process.parallel"]["meta"]["env_policy"] = \
            "sanitized (removed: BASH_ENV)"
        out = combine_runs([a, b])
        self.assertEqual(scoring.shell_env_policies(out)["process.parallel"],
                         "sanitized")


class ReportFieldsTest(unittest.TestCase):
    """R3: a result missing what reports print is bad input (exit 2),
    never a KeyError in report."""

    def test_missing_header_or_entry_fields_are_refused(self):
        from agentperf import scoring
        from agentperf.remote import _valid_fetched
        base = _load("macos-m1-pro")
        self.assertTrue(_valid_fetched(copy.deepcopy(base), base["label"]))
        bid = next(iter(base["benchmarks"]))
        cases = []
        for key in ("label", "profile", "system", "skipped"):
            doc = copy.deepcopy(base)
            del doc[key]
            cases.append(doc)
        for key in ("title", "unit"):
            doc = copy.deepcopy(base)
            del doc["benchmarks"][bid][key]
            cases.append(doc)
        for doc in cases:
            self.assertFalse(_valid_fetched(doc, base["label"]))
            with self.assertRaises(ValueError):
                scoring.validate_result(doc)

    def test_report_command_exits_cleanly(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        doc = _load("macos-m1-pro")
        del doc["profile"]
        path = os.path.join(tmp, "r.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        code, _o, err = _quiet_main(["report", path])
        self.assertEqual(code, 2, err)
        self.assertNotIn("Traceback", err)
        self.assertIn("profile", err)


if __name__ == "__main__":
    unittest.main()
