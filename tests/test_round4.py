"""Regression tests for the round-4 independent-review fixes (suite 0.3.0)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agentperf import corpus, diagnose, privacy, runner, scoring, util
from agentperf.cli import _check_combinable, _load_reference, combine_runs, main


def _entry(category, median, lower=True, weight=1.0, scored=True, samples=None, ops=1,
           title=None, meta=None, unit=None):
    return {"category": category, "title": title or category,
            "unit": unit or ("ms" if lower else "MB/s"),
            "lower_is_better": lower, "weight": weight, "scored": scored, "ops": ops,
            "median": median, "samples": samples or [median], "cv": 0.0,
            "noisy": False, "meta": meta or {}}


def _doc(label, benches, system=None):
    return {"label": label, "benchmarks": benches, "skipped": {},
            "system": system or {}, "schema": 1, "corpus": {"fixture_hash": "h1"}}


class WorkdirSafetyTest(unittest.TestCase):
    def test_nonempty_unowned_workdir_is_refused_and_untouched(self):
        tmp = tempfile.mkdtemp()
        try:
            repo = os.path.join(tmp, "repo")
            os.makedirs(repo)
            keep = os.path.join(repo, "keep.txt")
            with open(keep, "w") as fh:
                fh.write("mine")
            with self.assertRaises(SystemExit):
                runner.run_suite(tmp, log=lambda m: None)
            with open(keep) as fh:
                self.assertEqual(fh.read(), "mine")  # nothing was deleted
        finally:
            shutil.rmtree(tmp)

    def test_empty_workdir_is_claimed_with_marker(self):
        tmp = tempfile.mkdtemp()
        try:
            runner.prepare_workdir(tmp)
            self.assertTrue(runner.workdir_owned(tmp))
        finally:
            shutil.rmtree(tmp)

    def test_clean_removes_only_owned_entries(self):
        tmp = tempfile.mkdtemp()
        try:
            runner.prepare_workdir(tmp)
            os.makedirs(os.path.join(tmp, "scratch"))
            keep = os.path.join(tmp, "user-file.txt")
            with open(keep, "w") as fh:
                fh.write("keep")
            runner._clean_owned(tmp)
            self.assertTrue(os.path.exists(keep))       # not owned: stays
            self.assertFalse(os.path.exists(os.path.join(tmp, "scratch")))
            self.assertTrue(os.path.isdir(tmp))         # dir kept: not empty
        finally:
            shutil.rmtree(tmp)

    def test_unmatched_selector_fails_before_any_work(self):
        tmp = tempfile.mkdtemp()
        try:
            # Bad selectors are bad input (exit 2 via the CLI): ValueError.
            with self.assertRaises(ValueError):
                runner.run_suite(tmp, only=["process.shel"], log=lambda m: None)
            with self.assertRaises(ValueError):
                runner._validate_selectors(None, ["nope"])
        finally:
            shutil.rmtree(tmp)

    def test_all_failed_run_is_a_failure(self):
        tmp = tempfile.mkdtemp()
        try:
            work = os.path.join(tmp, "work")
            no_tools = {"git": None, "rg": None, "node": None, "tar": None,
                        "shell": None, "grep": None, "exec": None}
            with mock.patch.object(runner.tools, "find_tools", return_value=no_tools):
                with self.assertRaises(SystemExit):
                    runner.run_suite(work, only=["process.node"], log=lambda m: None)
        finally:
            shutil.rmtree(tmp)


class ProcessTest(unittest.TestCase):
    def test_failed_launch_is_not_scored(self):
        from agentperf.benches.process import _spawn_ms
        with self.assertRaises(RuntimeError):
            _spawn_ms([sys.executable, "-c", "import sys; sys.exit(3)"], 1)

    def test_hanging_launch_times_out(self):
        from agentperf.benches import process as pmod
        old = pmod.SPAWN_TIMEOUT_S
        pmod.SPAWN_TIMEOUT_S = 0.2
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                pmod._spawn_ms([sys.executable, "-c", "import time; time.sleep(5)"], 1)
        finally:
            pmod.SPAWN_TIMEOUT_S = old


class StatisticsTest(unittest.TestCase):
    def test_combined_interval_bootstraps_median_of_medians(self):
        # Pooled bootstrap of this shape used to give 10-10 around a point of 1000.
        ref = _doc("ref", {"process.shell": _entry("process", 10.0)})
        entry = _entry("process", 10.0)
        entry["run_samples"] = [[10.0] * 5] * 8 + [[1000.0] * 5]
        entry["samples"] = [s for r in entry["run_samples"] for s in r]
        entry["median"] = 10.0  # median of run medians
        doc = _doc("x", {"process.shell": entry})
        sc = scoring.score(doc, ref)
        lo, hi = sc["agentmark_ci"]
        self.assertLessEqual(lo, sc["agentmark"])
        self.assertGreaterEqual(hi, sc["agentmark"])
        self.assertIn("runs", sc["ci_kind"])

    def test_interval_never_excludes_point_for_single_run(self):
        ref = _doc("ref", {"process.shell": _entry("process", 10.0)})
        doc = _doc("x", {"process.shell": _entry(
            "process", 10.0, samples=[10.0] * 40 + [1000.0] * 5)})
        sc = scoring.score(doc, ref)
        self.assertLessEqual(sc["agentmark_ci"][0], sc["agentmark"])
        self.assertGreaterEqual(sc["agentmark_ci"][1], sc["agentmark"])

    def test_lw100_empty_is_not_available(self):
        lw = scoring.lw100(_doc("x", {}))
        self.assertIsNone(lw["seconds"])
        self.assertEqual(len(lw["missing"]), len(scoring.LW100_MIX))

    def test_lw100_common_uses_same_parts(self):
        full = _doc("a", {"process.shell": _entry("process", 10.0),
                          "process.python": _entry("process", 20.0)})
        part = _doc("b", {"process.shell": _entry("process", 20.0)})
        mine, theirs = scoring.lw100_common(full, part)
        self.assertEqual([l["label"] for l in mine["lines"]],
                         [l["label"] for l in theirs["lines"]])

    def test_method_difference_warns(self):
        ref = _doc("ref", {"transfer.read": _entry(
            "transfer", 1000.0, lower=False, meta={"method": "A"})})
        other = _doc("x", {"transfer.read": _entry(
            "transfer", 900.0, lower=False, meta={"method": "B"})})
        warnings = scoring.compatibility_warnings(other, ref)
        self.assertTrue(any("methods differ" in w for w in warnings), warnings)
        self.assertTrue(any("2%" in w for w in warnings), warnings)

    def test_fingerprint_changes_with_title_and_weights_match(self):
        a = _doc("a", {"process.shell": _entry("process", 1.0, title="old shell")})
        b = _doc("b", {"process.shell": _entry("process", 1.0, title="new shell")})
        self.assertNotEqual(scoring.fingerprint(a), scoring.fingerprint(b))
        self.assertTrue(any("fingerprint" in w
                            for w in scoring.compatibility_warnings(b, a)))


class CombineValidationTest(unittest.TestCase):
    def _pair(self):
        sysinfo = {"cpu_model": "CPU", "os_family": "macos"}
        a = _doc("m", {"process.shell": _entry("process", 5.0, samples=[5.0, 5.0])}, sysinfo)
        b = _doc("m", {"process.shell": _entry("process", 7.0, samples=[7.0, 7.0])},
                 dict(sysinfo))
        return a, b

    def test_combine_keeps_run_samples(self):
        a, b = self._pair()
        combined = combine_runs([a, b])
        entry = combined["benchmarks"]["process.shell"]
        self.assertEqual(entry["run_samples"], [[5.0, 5.0], [7.0, 7.0]])
        self.assertEqual(entry["median"], 6.0)

    def test_combine_rejects_different_corpus(self):
        a, b = self._pair()
        b["corpus"] = {"fixture_hash": "other"}
        with self.assertRaises(ValueError):
            combine_runs([a, b])

    def test_combine_rejects_different_method(self):
        a, b = self._pair()
        b["benchmarks"]["process.shell"]["meta"] = {"method": "other"}
        with self.assertRaises(ValueError):
            combine_runs([a, b])

    def test_merge_rejects_other_machine(self):
        a, b = self._pair()
        b["system"] = {"cpu_model": "Other", "os_family": "macos"}
        with self.assertRaises(ValueError):
            _check_combinable([a, b], "merge")

    def test_explicit_invalid_reference_errors(self):
        with self.assertRaises(ValueError):
            _load_reference("no-such-reference-xyz")
        self.assertEqual(main(["compare", __file__, "--ref", "no-such-reference-xyz"]), 2)


class CorpusAndCopyTest(unittest.TestCase):
    def test_tampered_corpus_content_is_regenerated(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "c")
            c = corpus.Corpus(root).ensure(log=lambda m: None)
            victim = c.path(c.files[123])
            with open(victim, "r+b") as fh:  # same size, different bytes
                fh.write(b"X")
            healed = corpus.Corpus(root).ensure(log=lambda m: None)
            with open(victim, "rb") as fh:
                self.assertNotEqual(fh.read(1), b"X")
            self.assertEqual(healed.fixture_hash, c.fixture_hash)
        finally:
            shutil.rmtree(tmp)

    def test_buffered_copy_moves_bytes(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(tmp, "a.bin")
            dst = os.path.join(tmp, "b.bin")
            data = os.urandom(100000)
            with open(src, "wb") as fh:
                fh.write(data)
            util.buffered_copy(src, dst)
            with open(dst, "rb") as fh:
                self.assertEqual(fh.read(), data)
        finally:
            shutil.rmtree(tmp)


class PrivacyRound4Test(unittest.TestCase):
    def test_volumes_user_subpaths_ipv6_and_nested_ids(self):
        doc = _doc("x", {"process.shell": _entry("process", 5.0)}, {
            "hostname": "h", "cpu_model": "CPU", "os_family": "macos",
            "spotlight": "/Volumes/Client Merger Documents:\n\tIndexing enabled.",
            "note": "at 2001:db8:1:2::5 in /Users/alice/Clients/secret-project",
        })
        doc["combined_runs"] = [{"run_id": "nested1", "started_at": "2026-10-01T01:02:03+00:00"}]
        doc["merged_runs"] = [{"run_id": "nested2", "started_at": "2026-10-02T03:04:05+00:00"}]
        clean, _ = privacy.anonymize(doc)
        text = json.dumps(clean)
        for needle in ("Client Merger", "secret-project", "2001:db8", "nested1",
                       "nested2", "01:02:03", "03:04:05", "alice"):
            self.assertNotIn(needle, text, needle)
        self.assertEqual(clean["system"]["spotlight"], "Indexing enabled")
        self.assertEqual(clean["combined_runs"], [{"started_at": "2026-10-01"}])


class RemoteTest(unittest.TestCase):
    def test_powershell_call_operator_for_quoted_python(self):
        from agentperf.remote import quote_powershell
        cmd = quote_powershell([r"C:\Program Files\Python\python.exe", "-u", "x.pyz"])
        self.assertTrue(cmd.startswith("& 'C:"), cmd)
        self.assertEqual(quote_powershell(["python", "-u", "x.pyz"]), "python -u x.pyz")


class ToolsCacheTest(unittest.TestCase):
    def test_partial_cached_tool_is_not_reused(self):
        from agentperf import tools
        tmp = tempfile.mkdtemp()
        try:
            target = os.path.join(tmp, "rg")
            with open(target, "wb") as fh:
                fh.write(b"half a binary")
            self.assertFalse(tools._cached_ok(target))
        finally:
            shutil.rmtree(tmp)


class DiagnoseRound4Test(unittest.TestCase):
    def test_compute_clause_is_gated_on_measurement(self):
        ref = _doc("ref", {
            "process.shell": _entry("process", 10.0),
            "files.read": _entry("files", 100.0),
            "compute.sha256": _entry("compute", 1000.0, lower=False),
            "compute.zlib": _entry("compute", 1000.0, lower=False),
            "compute.python": _entry("compute", 100.0),
        }, {"cpu_model": "R", "python": "3.14.0"})
        slow_all = _doc("slow", {
            "process.shell": _entry("process", 50.0),
            "files.read": _entry("files", 500.0),
            "compute.sha256": _entry("compute", 200.0, lower=False),
            "compute.zlib": _entry("compute", 200.0, lower=False),
            "compute.python": _entry("compute", 500.0),
        }, {"cpu_model": "S", "python": "3.9.6",
            "antivirus": [{"name": "X", "active": True}]})
        items = diagnose.causes(slow_all, ref)
        av = next(c for c in items if "Antivirus" in c["topic"])
        self.assertNotIn("stays competitive", " ".join(av["evidence"]))
        self.assertIn("slow here too", " ".join(av["evidence"]))
        # Numeric version compare: 3.9.6 < 3.14.0 must produce the cause.
        self.assertTrue(any("Python" in c["topic"] for c in items), [c["topic"] for c in items])

    def test_verdict_wording_and_wsl_only_on_windows(self):
        self.assertIn("2-3x slower", diagnose.verdict(400, "macos"))
        self.assertNotIn("WSL2", diagnose.verdict(100, "macos"))
        self.assertIn("WSL2", diagnose.verdict(100, "windows"))

    def test_power_plan_is_possible_without_point_gain(self):
        ref = _doc("ref", {"process.shell": _entry("process", 10.0)}, {"python": "3.12.0"})
        doc = _doc("win", {"process.shell": _entry("process", 30.0)},
                   {"os_family": "windows", "power_plan": "Balanced", "python": "3.12.0"})
        plan = next(c for c in diagnose.causes(doc, ref) if "power plan" in c["topic"].lower())
        self.assertEqual(plan["confidence"], "possible")
        self.assertFalse(plan["gain_estimated"])
        self.assertIsNone(plan["gain_points"])


class SearchTest(unittest.TestCase):
    def test_no_match_is_a_failure(self):
        from agentperf.benches.search import _search_ms
        tmp = tempfile.mkdtemp()
        try:
            grep = shutil.which("grep")
            if not grep:
                self.skipTest("no grep")
            with self.assertRaises(RuntimeError):
                _search_ms([grep, "-rn", "NEEDLE_THAT_IS_ABSENT", "."], tmp, expected=1, repeat=1)
        finally:
            shutil.rmtree(tmp)


class NetworkTest(unittest.TestCase):
    def test_context_is_per_host_and_notes_real_id(self):
        from agentperf.benches import network
        notes = {}

        class Ctx:
            def __init__(self):
                self._cache = {}

            def cached(self, key, make):
                if key not in self._cache:
                    self._cache[key] = make()
                return self._cache[key]

            def note(self, bid, **kw):
                notes.setdefault(bid, {}).update(kw)

        import socket as _socket

        class FakeRaw:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeTLS:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeCtx:
            def __init__(self, fail):
                self.fail = fail

            def wrap_socket(self, raw, server_hostname=None):
                if self.fail and server_hostname == "blocked.example":
                    raise OSError("blocked")
                return FakeTLS()

        def fake_connect(addr, timeout=None):
            if addr[0] == "blocked.example":
                raise OSError("unreachable")
            return FakeRaw()

        with mock.patch.object(network.ssl, "create_default_context",
                               lambda: FakeCtx(False)), \
                mock.patch.object(network.socket, "create_connection", fake_connect):
            from agentperf.registry import Skip
            ctx = Ctx()
            with self.assertRaises(Skip):
                network._context_for(ctx, "blocked.example", "network.tls_anthropic")
            ctx2 = Ctx()
            network._context_for(ctx2, "open.example", "network.tls_openai")
            self.assertEqual(notes.get("network.tls_openai"), {"tls_verified": True})


class HtmlRound4Test(unittest.TestCase):
    def test_compare_ranks_on_common_tests_and_shows_warnings(self):
        from agentperf.report_html import write_html
        ref = _doc("ref", {
            "process.shell": _entry("process", 10.0),
            "files.read": _entry("files", 100.0),
            "compute.sha256": _entry("compute", 1000.0, lower=False),
        })
        # A wins on the full set (extra fast compute) but loses on common tests.
        a = _doc("AAA", {
            "process.shell": _entry("process", 100.0),
            "files.read": _entry("files", 100.0),
            "compute.sha256": _entry("compute", 8000.0, lower=False),
        })
        b = _doc("BBB", {
            "process.shell": _entry("process", 50.0),
            "files.read": _entry("files", 100.0),
        })
        b["corpus"] = {"fixture_hash": "different"}
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "c.html")
            write_html([a, b], ref, path)
            with open(path, encoding="utf-8") as fh:
                page = fh.read()
            self.assertIn("Comparability (BBB)", page)
            self.assertIn("common tests", page)
            self.assertIn("coverage", page)
            # BBB (faster on the shared process test) must appear before AAA
            # in the AgentMark chart order (tiles are emitted in rank order).
            self.assertLess(page.index("BBB"), page.index("AAA"))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
