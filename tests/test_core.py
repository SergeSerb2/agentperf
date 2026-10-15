"""Unit tests for the corpus, statistics, scoring, and CLI plumbing (stdlib unittest)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile

from agentperf import corpus, scoring, util
from agentperf.registry import CATEGORY_WEIGHTS


def _entry(category, median, lower=True, weight=1.0, scored=True, samples=None, ops=1):
    return {"category": category, "title": category, "unit": "ms" if lower else "MB/s",
            "lower_is_better": lower, "weight": weight, "scored": scored, "ops": ops,
            "median": median, "samples": samples or [median], "cv": 0.0, "noisy": False, "meta": {}}


def _doc(label, benches):
    return {"label": label, "profile": "standard", "benchmarks": benches,
            "skipped": {}, "system": {}}


class CorpusTest(unittest.TestCase):
    def test_paths_and_content_are_deterministic(self):
        self.assertEqual(corpus._relpath(123), corpus._relpath(123))
        self.assertEqual(corpus._content(97), corpus._content(97))
        self.assertIn(corpus.NEEDLE.encode(), corpus._content(97))
        self.assertNotIn(corpus.NEEDLE.encode(), corpus._content(98))

    def test_known_fixture_hash(self):
        # Changing generation must bump CORPUS_FORMAT; this pins the current output.
        tmp = tempfile.mkdtemp()
        try:
            c = corpus.Corpus(os.path.join(tmp, "c")).ensure(log=lambda m: None)
            self.assertEqual(len(c.files), corpus.FILE_COUNT)
            self.assertEqual(c.fixture_hash, "3d38cb124fb291d9")
            again = corpus.Corpus(os.path.join(tmp, "c")).ensure(log=lambda m: None)
            self.assertEqual(again.fixture_hash, c.fixture_hash)
        finally:
            shutil.rmtree(tmp)


    def test_damaged_corpus_cache_is_regenerated(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "c")
            c = corpus.Corpus(root).ensure(log=lambda m: None)
            victim = c.path(c.files[123])
            os.remove(victim)
            healed = corpus.Corpus(root).ensure(log=lambda m: None)
            self.assertTrue(os.path.exists(victim))
            self.assertEqual(healed.fixture_hash, c.fixture_hash)
        finally:
            shutil.rmtree(tmp)


class RegistryTest(unittest.TestCase):
    def test_node_spawn_is_an_unscored_diagnostic(self):
        from agentperf import benches  # noqa: F401
        from agentperf.registry import REGISTRY
        bench = next(b for b in REGISTRY if b.id == "diag.node_spawn")
        self.assertFalse(bench.scored)


class GitProbeTest(unittest.TestCase):
    def test_unresponsive_git_skips_instead_of_hanging(self):
        import stat
        from agentperf.benches.git import Repo
        from agentperf.registry import Skip
        tmp = tempfile.mkdtemp()
        try:
            fake = os.path.join(tmp, "git")
            with open(fake, "w", encoding="utf-8") as fh:
                fh.write("#!/bin/sh\nexit 1\n")
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)

            class _Ctx:
                workdir = tmp

                def need(self, *names):
                    return (fake,)

            with self.assertRaises(Skip):
                Repo(_Ctx(), tmp)
        finally:
            shutil.rmtree(tmp)


class RunnerLockTest(unittest.TestCase):
    def test_second_run_in_same_workdir_is_refused(self):
        from agentperf import runner
        tmp = tempfile.mkdtemp()
        try:
            lock = runner._acquire_lock(tmp)
            with self.assertRaises(SystemExit):
                runner._acquire_lock(tmp)
            os.remove(lock)
            runner._acquire_lock(tmp)  # free again after removal
        finally:
            shutil.rmtree(tmp)


class StatsTest(unittest.TestCase):
    def test_median_and_percentile(self):
        self.assertEqual(util.median([3, 1, 2]), 2)
        self.assertEqual(util.median([4, 1, 2, 3]), 2.5)
        self.assertAlmostEqual(util.percentile([1, 2, 3, 4, 5], 0.9), 4.6)

    def test_geomean_weights(self):
        self.assertAlmostEqual(util.geomean([1, 4]), 2.0)
        self.assertAlmostEqual(util.geomean([1, 8], [2, 1]), 2.0)

    def test_cv(self):
        self.assertEqual(util.cv([5.0]), 0.0)
        self.assertAlmostEqual(util.cv([9.0, 11.0]), 0.1414, places=3)


class ScoringTest(unittest.TestCase):
    def setUp(self):
        self.ref = _doc("ref", {
            "process.shell": _entry("process", 10.0),
            "files.read": _entry("files", 100.0, ops=2000),
            "transfer.read": _entry("transfer", 1000.0, lower=False),
            "network.dns": _entry("network", 5.0),
            "diag.login_shell": _entry("diag", 40.0, scored=False),
        })

    def test_reference_scores_1000(self):
        sc = scoring.score(self.ref, self.ref)
        self.assertAlmostEqual(sc["agentmark"], 1000.0)
        self.assertAlmostEqual(sc["reach"], 1000.0)
        self.assertAlmostEqual(sc["coverage"], 1.0)

    def test_twice_as_fast_scores_2000(self):
        fast = _doc("fast", {
            "process.shell": _entry("process", 5.0),
            "files.read": _entry("files", 50.0, ops=2000),
            "transfer.read": _entry("transfer", 2000.0, lower=False),
            "network.dns": _entry("network", 10.0),
        })
        sc = scoring.score(fast, self.ref)
        self.assertAlmostEqual(sc["agentmark"], 2000.0)
        self.assertAlmostEqual(sc["reach"], 500.0)  # network is not part of AgentMark
        self.assertNotIn("diag.login_shell", sc["benchmarks"])

    def test_missing_benchmark_lowers_coverage_not_score(self):
        partial = _doc("partial", {"process.shell": _entry("process", 5.0)})
        sc = scoring.score(partial, self.ref)
        self.assertAlmostEqual(sc["agentmark"], 2000.0)
        expected = CATEGORY_WEIGHTS["process"] / sum(
            CATEGORY_WEIGHTS[c] for c in ("process", "files", "transfer"))
        self.assertAlmostEqual(sc["coverage"], expected)

    def test_bootstrap_interval_contains_point(self):
        noisy = _doc("noisy", {"process.shell": _entry("process", 10.0, samples=[8, 9, 10, 11, 12])})
        sc = scoring.score(noisy, self.ref)
        lo, hi = sc["agentmark_ci"]
        self.assertLessEqual(lo, sc["agentmark"])
        self.assertGreaterEqual(hi, sc["agentmark"])

    def test_compatibility_warnings(self):
        ref = _doc("ref", {"process.shell": _entry("process", 10.0)})
        ref["corpus"] = {"fixture_hash": "aaa"}
        ref["schema"] = 1
        same = json.loads(json.dumps(ref))
        self.assertEqual(scoring.compatibility_warnings(same, ref), [])
        other_corpus = json.loads(json.dumps(ref))
        other_corpus["corpus"] = {"fixture_hash": "bbb"}
        self.assertTrue(any("corpus" in w for w in scoring.compatibility_warnings(other_corpus, ref)))
        heavier = json.loads(json.dumps(ref))
        heavier["benchmarks"]["process.shell"]["weight"] = 9.0
        self.assertTrue(any("definitions differ" in w
                            for w in scoring.compatibility_warnings(heavier, ref)))

    def test_equal_weights_change_only_the_weights(self):
        mixed = _doc("mixed", {
            "process.shell": _entry("process", 5.0),
            "files.read": _entry("files", 100.0, ops=2000),
            "transfer.read": _entry("transfer", 250.0, lower=False),
        })
        default = scoring.score(mixed, self.ref)["agentmark"]
        equal = scoring.score(mixed, self.ref,
                              category_weights=scoring.equal_category_weights())["agentmark"]
        # ratios 2, 1, 0.25: equal-weight geomean is (2*1*0.25)^(1/3)
        self.assertAlmostEqual(equal, 1000 * 0.5 ** (1.0 / 3.0))
        self.assertNotAlmostEqual(default, equal)

    def test_lw100_uses_per_op_cost(self):
        lw = scoring.lw100(self.ref)
        shell = next(l for l in lw["lines"] if l["label"].startswith("Shell start"))
        reads = next(l for l in lw["lines"] if l["label"].startswith("File reads"))
        self.assertAlmostEqual(shell["seconds"], 79 * 10.0 / 1000)
        self.assertAlmostEqual(reads["ms_each"], 100.0 / 2000)
        self.assertIn("Commits", lw["missing"])


class PackagingTest(unittest.TestCase):
    def test_pyz_can_rebuild_itself_from_inside_a_pyz(self):
        # Regression: `agentperf remote` run from agentperf.pyz used os.walk
        # on a path inside the zip and silently shipped an empty archive.
        import subprocess
        import sys
        from agentperf.remote import build_pyz
        tmp = tempfile.mkdtemp()
        try:
            outer = build_pyz(os.path.join(tmp, "outer.pyz"))
            inner = os.path.join(tmp, "inner.pyz")
            driver = ("import sys; sys.path.insert(0, sys.argv[1]);"
                      "from agentperf.remote import build_pyz; build_pyz(sys.argv[2])")
            proc = subprocess.run([sys.executable, "-c", driver, outer, inner],
                                  cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            with zipfile.ZipFile(inner) as zf:
                names = set(zf.namelist())
            self.assertIn("agentperf/cli.py", names)
            self.assertIn("agentperf/references/DEFAULT", names)
            self.assertGreater(os.path.getsize(inner), 10000)
        finally:
            shutil.rmtree(tmp)

    def test_remote_quoting_and_labels(self):
        from agentperf.remote import quote_posix, quote_powershell, sanitize_label
        self.assertEqual(quote_posix(["run", "--label", "a b;rm"]), "run --label 'a b;rm'")
        self.assertEqual(quote_powershell(["python", "-u", "x.pyz", "a'b"]), "python -u x.pyz 'a''b'")
        self.assertEqual(sanitize_label("my-mac_1.2"), "my-mac_1.2")
        with self.assertRaises(SystemExit):
            sanitize_label("bad label; rm -rf")

    def test_pyz_contains_package_and_entry_point(self):
        from agentperf.remote import build_pyz
        tmp = tempfile.mkdtemp()
        try:
            path = build_pyz(os.path.join(tmp, "a.pyz"))
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
            self.assertIn("__main__.py", names)
            self.assertIn("agentperf/cli.py", names)
            self.assertIn("agentperf/benches/git.py", names)
            self.assertFalse(any("__pycache__" in n for n in names))
        finally:
            shutil.rmtree(tmp)

    def test_merge_command(self):
        from agentperf.cli import main
        tmp = tempfile.mkdtemp()
        try:
            a = _doc("m", {"process.shell": _entry("process", 5.0)})
            a["skipped"] = {"network.dns": "excluded"}
            b = _doc("m", {"network.dns": _entry("network", 3.0)})
            pa, pb, out = (os.path.join(tmp, n) for n in ("a.json", "b.json", "out.json"))
            for path, doc in ((pa, a), (pb, b)):
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(doc, fh)
            self.assertEqual(main(["merge", pa, pb, "--out", out]), 0)
            with open(out, encoding="utf-8") as fh:
                merged = json.load(fh)
            self.assertEqual(sorted(merged["benchmarks"]), ["network.dns", "process.shell"])
            self.assertEqual(merged["skipped"], {})
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
