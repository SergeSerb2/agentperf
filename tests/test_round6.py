"""Regression tests for the round-6 independent verification findings.

Each test names the finding it pins (review-r4-codex-verify.md). They use
only temp folders and mocks: no network, no real home directories.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agentperf import privacy, runner, scoring, util


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


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)


class WorkdirRound6Test(_Tmp):
    def test_c1_forged_manifest_does_not_claim_workdir(self):
        work = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(work, "corpus"))
        os.makedirs(os.path.join(work, "repo"))
        keep = os.path.join(work, "repo", "keep.txt")
        with open(keep, "w") as fh:
            fh.write("mine")
        with open(os.path.join(work, "corpus", "corpus.json"), "w") as fh:
            json.dump({"fixture_hash": "anything", "files": []}, fh)
        with self.assertRaises(SystemExit):
            runner.prepare_workdir(work)
        with open(keep) as fh:
            self.assertEqual(fh.read(), "mine")

    def test_new1_clean_keeps_unknown_archives(self):
        runner.prepare_workdir(self.tmp)
        os.makedirs(os.path.join(self.tmp, "corpus"))
        with open(os.path.join(self.tmp, "corpus", "corpus.json"), "w") as fh:
            json.dump({"fixture_hash": "3d38cb124fb291d9"}, fh)
        names = ["corpus-client-backup.tar.gz",
                 "corpus-deadbeefdeadbeef.tar.gz",
                 "corpus-3d38cb124fb291d9.tar.gz",
                 "corpus-aaaabbbbccccdddd.tar.gz"]
        for name in names:
            with open(os.path.join(self.tmp, name), "w") as fh:
                fh.write("x")
        runner._record_owned_archive(self.tmp, "corpus-aaaabbbbccccdddd.tar.gz")
        runner._clean_owned(self.tmp)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, names[0])), names[0])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, names[1])), names[1])
        # Round 7: a manifest hash alone never owns an archive; only the
        # generation ledger does, so names[2] now survives too.
        self.assertTrue(os.path.exists(os.path.join(self.tmp, names[2])), names[2])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, names[3])), names[3])

    def test_c3_clean_never_removes_the_lock(self):
        runner.prepare_workdir(self.tmp)
        lock = os.path.join(self.tmp, runner.LOCK_NAME)
        with open(lock, "w") as fh:
            fh.write("123\n")
        os.makedirs(os.path.join(self.tmp, "scratch"))
        runner._clean_owned(self.tmp)
        self.assertTrue(os.path.exists(lock))  # released last, by the owner
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "scratch")))


class ProcessRound6Test(unittest.TestCase):
    def test_c2_parallel_spawn_failure_reaps_started_children(self):
        from agentperf.benches import process as pmod

        class Ctx:
            def need(self, *names):
                return ("bash",)

            def note(self, bid, **kw):
                pass

        fake = mock.Mock()
        fake.returncode = 0
        sample = pmod.parallel_spawn(Ctx())
        with mock.patch.object(pmod.subprocess, "Popen",
                               side_effect=[fake, OSError("cannot fork")]):
            with self.assertRaises(OSError):
                sample()
        self.assertTrue(fake.kill.called)
        self.assertTrue(fake.wait.called)

    def test_new3_shell_env_is_sanitized(self):
        env = util.shell_env({"BASH_ENV": "/tmp/evil", "ENV": "/tmp/evil",
                              "SHELLOPTS": "x", "BASHOPTS": "y", "PATH": "/bin"})
        for key in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS"):
            self.assertNotIn(key, env)
        self.assertEqual(env["PATH"], "/bin")
        node_env = util.shell_env({"NODE_OPTIONS": "--require evil", "PATH": "/bin"},
                                  node=True)
        self.assertNotIn("NODE_OPTIONS", node_env)

    @unittest.skipIf(os.name == "nt", "POSIX bash probe")
    def test_new3_scored_bash_ignores_bash_env(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("no bash")
        from agentperf.benches import process as pmod

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        probe = os.path.join(tmp, "env.sh")
        marker = os.path.join(tmp, "ran")
        with open(probe, "w") as fh:
            fh.write("touch {}\n".format(marker))
        argv = [bash, "--noprofile", "--norc", "-c", "true"]
        with mock.patch.dict(os.environ, {"BASH_ENV": probe}):
            pmod._spawn_ms(argv, 2, env=util.shell_env())
        self.assertFalse(os.path.exists(marker))


class BootstrapRound6Test(unittest.TestCase):
    def test_c6_bootstrap_keeps_whole_run_correlation(self):
        # Eight tests that move together by run: shared resampling must
        # give a far wider interval than independent resampling.
        benches, ref_benches = {}, {}
        levels = [1.0, 10.0, 100.0]
        for i in range(8):
            bid = "process.t{}".format(i)
            entry = _entry("process", 10.0)
            entry["run_samples"] = [[v] * 3 for v in levels]
            entry["samples"] = [s for r in entry["run_samples"] for s in r]
            benches[bid] = entry
            ref_benches[bid] = _entry("process", 10.0)
        sc = scoring.score(_doc("x", benches), _doc("ref", ref_benches),
                           rounds=400, seed=11)
        lo, hi = sc["agentmark_ci"]
        self.assertLessEqual(lo, sc["agentmark"])
        self.assertGreaterEqual(hi, sc["agentmark"])
        self.assertGreater(hi / lo, 8.0, sc["agentmark_ci"])


class CombineRound6Test(unittest.TestCase):
    def _pair(self):
        sysinfo = {"cpu_model": "CPU", "os_family": "macos", "python": "3.12.0"}
        a = _doc("m", {"process.shell": _entry("process", 5.0, samples=[5.0, 5.0])},
                 dict(sysinfo))
        b = _doc("m", {"process.shell": _entry("process", 7.0, samples=[7.0, 7.0])},
                 dict(sysinfo))
        a["tool_versions"] = {"shell": "GNU bash, version 5.3.9"}
        b["tool_versions"] = {"shell": "GNU bash, version 5.3.9"}
        return a, b

    def test_c9_combine_rejects_different_python(self):
        from agentperf.cli import combine_runs
        a, b = self._pair()
        b["system"]["python"] = "3.9.6"
        with self.assertRaises(ValueError):
            combine_runs([a, b])

    def test_c9_combine_rejects_different_shell_version(self):
        from agentperf.cli import combine_runs
        a, b = self._pair()
        b["tool_versions"]["shell"] = "GNU bash, version 3.2.57"
        with self.assertRaises(ValueError):
            combine_runs([a, b])

    def test_combine_preserves_run_identity_for_missing_bench(self):
        from agentperf.cli import combine_runs
        a, b = self._pair()
        a["benchmarks"]["files.read"] = _entry("files", 100.0, samples=[100.0])
        combined = combine_runs([a, b])
        entry = combined["benchmarks"]["files.read"]
        self.assertEqual(entry["run_samples"], [[100.0], []])
        self.assertEqual(entry["runs"], 1)


class FingerprintRound6Test(_Tmp):
    def test_new4_historical_weights_are_detected(self):
        old_weights = dict(scoring.CATEGORY_WEIGHTS)
        old_weights["process"] = 99.0
        old = _doc("old", {"process.shell": _entry("process", 10.0)})
        old["scoring_config"] = scoring.scoring_config_snapshot(old_weights)
        old["fingerprint"] = scoring.fingerprint(old)
        current = _doc("new", {"process.shell": _entry("process", 9.0)})
        current["scoring_config"] = scoring.scoring_config_snapshot()
        current["fingerprint"] = scoring.fingerprint(current)
        warnings = scoring.compatibility_warnings(current, old)
        self.assertTrue(any("weight" in w or "fingerprint" in w for w in warnings),
                        warnings)

    def test_new4_stored_fingerprint_is_validated(self):
        doc = _doc("x", {"process.shell": _entry("process", 10.0)})
        doc["fingerprint"] = "bogus0000000000"
        ref = _doc("ref", {"process.shell": _entry("process", 10.0)})
        warnings = scoring.compatibility_warnings(doc, ref)
        self.assertTrue(any("stored fingerprint" in w for w in warnings), warnings)

    def test_new4_merge_regenerates_fingerprint(self):
        from agentperf.cli import main
        a = _doc("m", {"process.shell": _entry("process", 5.0)},
                 {"cpu_model": "CPU", "os_family": "macos", "python": "3.12.0"})
        b = _doc("m", {"files.read": _entry("files", 50.0)},
                 {"cpu_model": "CPU", "os_family": "macos", "python": "3.12.0"})
        pa, pb, out = (os.path.join(self.tmp, n) for n in ("a.json", "b.json", "o.json"))
        for path, doc in ((pa, a), (pb, b)):
            with open(path, "w") as fh:
                json.dump(doc, fh)
        self.assertEqual(main(["merge", pa, pb, "--out", out]), 0)
        with open(out) as fh:
            merged = json.load(fh)
        self.assertEqual(merged["fingerprint"], scoring.fingerprint(merged))
        self.assertIn("scoring_config", merged)

    def test_new4_share_regenerates_fingerprint(self):
        doc = _doc("x", {"process.shell": _entry("process", 5.0)},
                   {"cpu_model": "CPU", "os_family": "macos"})
        doc["scoring_config"] = scoring.scoring_config_snapshot()
        doc["fingerprint"] = scoring.fingerprint(doc)
        clean, _changes = privacy.anonymize(doc)
        self.assertEqual(clean["fingerprint"], scoring.fingerprint(clean))


class SearchRound6Test(_Tmp):
    def test_c12_expected_counts_come_from_corpus_files(self):
        from agentperf.corpus import Corpus
        c = Corpus(os.path.join(self.tmp, "c"))
        os.makedirs(c.tree)
        files = {
            "a.py": 'marker = "NEEDLE_TOKEN_42"\ndef compute_agent_123(x):\n',
            "b.py": "def compute_cache_007(y):\nplain\n",
            "c.py": "nothing here\n",
        }
        for rel, text in files.items():
            with open(os.path.join(c.tree, rel), "w") as fh:
                fh.write(text)
        c.files = sorted(files)
        counts = c.expected_search_counts()
        self.assertEqual(counts["needle_files"], 1)
        self.assertEqual(counts["needle_lines"], 1)
        self.assertEqual(counts["regex_lines"], 2)

    def test_c12_tool_count_must_match_independent_expected(self):
        from agentperf.benches import search
        from agentperf.registry import Skip
        fake = subprocess.CompletedProcess(["rg"], 0, stdout=b"only-one.py\n",
                                           stderr=b"")
        with mock.patch.object(search, "run", return_value=fake):
            with self.assertRaises(Skip):
                search._validate_lines(["rg"], self.tmp, 62, "rg literal")

    def test_c12_every_repetition_is_validated(self):
        from agentperf.benches import search

        def make(n):
            return subprocess.CompletedProcess(
                ["rg"], 0, stdout=("\n".join("f%d" % i for i in range(n)) + "\n").encode(),
                stderr=b"")

        with mock.patch.object(search, "run", side_effect=[make(62), make(1), make(62)]):
            with self.assertRaises(RuntimeError):
                search._search_ms(["rg"], self.tmp, expected=62, repeat=3)


class PrivacyRound6Test(unittest.TestCase):
    def test_c14_space_bearing_paths_are_fully_scrubbed(self):
        doc = _doc("x", {"process.shell": _entry("process", 5.0)}, {
            "cpu_model": "CPU", "os_family": "macos",
            "note": "docs in /Volumes/Client Merger Documents/Deals/secret plan.txt",
            "win": r"repo at C:\Users\Alice Smith\Clients\Secret App\main.py",
        })
        clean, _ = privacy.anonymize(doc)
        text = json.dumps(clean)
        for needle in ("Merger", "Deals", "secret plan", "Smith",
                       "Clients", "Secret App"):
            self.assertNotIn(needle, text, needle)


class CopyRound6Test(_Tmp):
    def test_new5_copy_tree_missing_source_raises(self):
        with self.assertRaises(OSError):
            util.copy_tree_buffered(os.path.join(self.tmp, "absent"),
                                    os.path.join(self.tmp, "dst"))

    def test_new5_copy_tree_reports_counts(self):
        src = os.path.join(self.tmp, "src")
        os.makedirs(os.path.join(src, "sub"))
        with open(os.path.join(src, "a.bin"), "wb") as fh:
            fh.write(b"x" * 10)
        with open(os.path.join(src, "sub", "b.bin"), "wb") as fh:
            fh.write(b"y" * 5)
        files, nbytes = util.copy_tree_buffered(src, os.path.join(self.tmp, "dst"))
        self.assertEqual((files, nbytes), (2, 15))


class ToolsRound6Test(_Tmp):
    def test_c22_cached_tool_must_exit_zero(self):
        from agentperf import tools
        target = os.path.join(self.tmp, "rg")
        with open(target, "wb") as fh:
            fh.write(b"fake")
        bad = subprocess.CompletedProcess([target], 1, stdout=b"rg 1.0\n", stderr=b"")
        good = subprocess.CompletedProcess([target], 0, stdout=b"rg 1.0\n", stderr=b"")
        with mock.patch.object(tools, "run", return_value=bad):
            self.assertFalse(tools._cached_ok(target))
        with mock.patch.object(tools, "run", return_value=good):
            self.assertTrue(tools._cached_ok(target))


class NetworkRound6Test(unittest.TestCase):
    def test_c18_cached_context_still_notes_each_benchmark(self):
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

        class FakeRaw:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakeTLS(FakeRaw):
            pass

        class FakeCtx:
            def wrap_socket(self, raw, server_hostname=None):
                return FakeTLS()

        with mock.patch.object(network.ssl, "create_default_context", lambda: FakeCtx()), \
                mock.patch.object(network.socket, "create_connection",
                                  lambda addr, timeout=None: FakeRaw()):
            ctx = Ctx()
            network._context_for(ctx, "api.example", "network.tls_x")
            network._context_for(ctx, "api.example", "network.ttfb_x")
        self.assertEqual(notes.get("network.tls_x"), {"tls_verified": True})
        self.assertEqual(notes.get("network.ttfb_x"), {"tls_verified": True})


class RemoteRound6Test(unittest.TestCase):
    def test_new8_non_object_json_is_a_clean_invalid_result(self):
        from agentperf.remote import _valid_fetched
        self.assertFalse(_valid_fetched([], "host1"))
        self.assertFalse(_valid_fetched("result", "host1"))
        self.assertFalse(_valid_fetched({"label": "other", "benchmarks": {}}, "host1"))
        self.assertFalse(_valid_fetched({"label": "host1"}, "host1"))
        # Round 7: entries need finite medians to count as a result.
        self.assertTrue(_valid_fetched(
            {"label": "host1",
             "benchmarks": {"process.shell": {"median": 1.0}}}, "host1"))


class ReportRound6Test(_Tmp):
    def test_new6_placement_interval_uses_restricted_score(self):
        from agentperf import diagnose
        shared = {"process.shell": _entry("process", 10.0, samples=[10.0] * 5)}
        result = _doc("mine", dict(shared))
        result["benchmarks"]["files.read"] = _entry(
            "files", 1000.0, samples=[1000.0] * 5)
        reference = _doc("ref", dict(shared))
        reference["benchmarks"]["files.read"] = _entry("files", 10.0,
                                                       samples=[10.0] * 5)
        other = _doc("other", {"process.shell": _entry("process", 20.0)})
        place = diagnose.placement(result, reference, [other])
        restricted = scoring.score(result, reference,
                                   restrict={"process.shell"})
        self.assertAlmostEqual(place["agentmark"], restricted["agentmark"])
        self.assertIsNotNone(place.get("agentmark_ci"))
        self.assertAlmostEqual(place["agentmark_ci"][0],
                               restricted["agentmark_ci"][0], places=6)
        self.assertAlmostEqual(place["agentmark_ci"][1],
                               restricted["agentmark_ci"][1], places=6)

    def test_new6_estimate_range_uses_common_restriction(self):
        from agentperf.report_html import Machine
        common = {"process.shell"}
        ref = _doc("ref", {"process.shell": _entry("process", 10.0),
                           "files.read": _entry("files", 10.0)})
        mid = _doc("est", {"process.shell": _entry("process", 20.0),
                           "files.read": _entry("files", 20.0)})
        low = _doc("est", {"process.shell": _entry("process", 20.0),
                           "files.read": _entry("files", 5000.0)})
        high = _doc("est", {"process.shell": _entry("process", 20.0),
                            "files.read": _entry("files", 1.0)})
        m = Machine(mid, ref, common, estimated=True, low=low, high=high)
        expected = scoring.score(mid, ref, restrict=common)["agentmark"]
        self.assertAlmostEqual(m.range[0], expected)
        self.assertAlmostEqual(m.range[1], expected)

    def test_new9_scored_common_count_excludes_diagnostics(self):
        from agentperf.report_html import write_html
        benches = {"process.shell": _entry("process", 10.0),
                   "network.dns": _entry("network", 5.0),
                   "diag.node_spawn": _entry("diag", 3.0, scored=False)}
        ref = _doc("ref", dict(benches))
        doc = _doc("mine", dict(benches))
        out = os.path.join(self.tmp, "c.html")
        write_html([doc], ref, out)
        with open(out, encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("1 scored tests every machine ran", page)
        self.assertNotIn("2 scored tests every machine ran", page)
        self.assertIn("on 1 scored common tests", page)

    def test_c8_compare_discloses_partial_lw100(self):
        from agentperf.report_text import compare_report
        ref = _doc("ref", {"process.shell": _entry("process", 10.0)})
        doc = _doc("mine", {"process.shell": _entry("process", 20.0)})
        text = compare_report([doc], ref)
        self.assertIn("covers 1 of 8 parts", text)
        self.assertIn("missing:", text)

    def test_s16_reports_do_not_call_gains_upper_bounds(self):
        from agentperf.report_text import why_summary
        from agentperf.cli import _all_bundled_references, _load_reference
        reference = _load_reference(None)
        weak = min(_all_bundled_references(),
                   key=lambda d: scoring.score(d, reference, rounds=30)["agentmark"])
        text = why_summary(weak, reference)
        self.assertNotIn("upper bound", text.lower())
        self.assertNotIn("worth up to", text.lower())


class LiveRound6Test(_Tmp):
    def _repo(self, name, test_body, numbers="def clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n",
              extra=None):
        repo = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(repo, "calcbox"))
        os.makedirs(os.path.join(repo, "tests"))
        with open(os.path.join(repo, "calcbox", "__init__.py"), "w") as fh:
            fh.write("")
        with open(os.path.join(repo, "calcbox", "numbers.py"), "w") as fh:
            fh.write(numbers)
        with open(os.path.join(repo, "tests", "__init__.py"), "w") as fh:
            fh.write("")
        with open(os.path.join(repo, "tests", "test_numbers.py"), "w") as fh:
            fh.write(test_body)
        for rel, text in (extra or {}).items():
            with open(os.path.join(repo, rel), "w") as fh:
                fh.write(text)
        return repo

    _ONE_TEST = ("import unittest\n\n\nclass T(unittest.TestCase):\n"
                 "    def test_one(self):\n        print('Ran 99 tests in 0.001s')\n"
                 "        self.assertTrue(True)\n")

    def test_c17_forged_stdout_count_does_not_pass(self):
        from agentperf.live import tasks
        repo = self._repo("forged", self._ONE_TEST)
        ok, detail = tasks._run_tests(repo, min_tests=8)
        self.assertFalse(ok, detail)
        self.assertIn("1", detail)

    def test_c17_hidden_tests_ignore_repo_unittest_shadow(self):
        from agentperf.live import tasks
        repo = self._repo("shadow", self._ONE_TEST,
                          extra={"unittest.py": "raise ImportError('shadowed')\n"})
        with mock.patch.dict(os.environ, {"PYTHONPATH": repo}):
            ok, detail = tasks._run_hidden(repo)
        self.assertTrue(ok, detail)

    def test_c17_hidden_tests_still_fail_bad_clamp(self):
        from agentperf.live import tasks
        repo = self._repo("bad", self._ONE_TEST,
                          numbers="def clamp(v, lo, hi):\n    return v\n")
        ok, _detail = tasks._run_hidden(repo)
        self.assertFalse(ok)

    @unittest.skipIf(os.name == "nt", "process-group test is POSIX-only")
    def test_c15_descendant_is_killed_after_parent_exits(self):
        import time
        from agentperf.live.harness import _kill_tree
        pidfile = os.path.join(self.tmp, "child.pid")
        child = (
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *a: None)\n"
            "open(sys.argv[1], 'w').write(__import__('os').getpid().__str__())\n"
            "time.sleep(60)\n")
        parent = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', {!r}, sys.argv[1]])\n".format(child))
        proc = subprocess.Popen(
            [sys.executable, "-c", parent, pidfile],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        child_pid = None
        try:
            proc.wait(timeout=20)  # parent exits; grandchild keeps running
            for _ in range(100):
                if os.path.exists(pidfile):
                    with open(pidfile) as fh:
                        text = fh.read().strip()
                    if text:
                        child_pid = int(text)
                        break
                time.sleep(0.05)
            self.assertIsNotNone(child_pid)
            os.kill(child_pid, 0)  # still alive before the kill
            _kill_tree(proc)
            for _ in range(100):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("grandchild survived _kill_tree")
        finally:
            if child_pid:
                try:
                    os.kill(child_pid, 9)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
