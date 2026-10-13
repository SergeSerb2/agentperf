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
    return {"label": label, "benchmarks": benches, "skipped": {}, "system": {}}


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

    def test_lw100_uses_per_op_cost(self):
        lw = scoring.lw100(self.ref)
        shell = next(l for l in lw["lines"] if l["label"].startswith("Shell start"))
        reads = next(l for l in lw["lines"] if l["label"].startswith("File reads"))
        self.assertAlmostEqual(shell["seconds"], 79 * 10.0 / 1000)
        self.assertAlmostEqual(reads["ms_each"], 100.0 / 2000)
        self.assertIn("Commits", lw["missing"])


class PackagingTest(unittest.TestCase):
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
