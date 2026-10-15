"""Round-8 regression tests: blockers from review-r7-codex-verify.md.

Each test names the finding it pins (F1-F7 plus the two docs items).
Temp folders and mocks only: no network, no real home folders, no SSH.
Invented names only."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import math
import os
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _quiet_main(argv):
    from agentperf.cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _entry(category, median, lower=True, weight=1.0, scored=True, meta=None):
    return {"category": category, "title": category,
            "unit": "ms" if lower else "MB/s",
            "lower_is_better": lower, "weight": weight, "scored": scored,
            "ops": 1, "median": median, "samples": [median], "cv": 0.0,
            "noisy": False, "meta": meta or {}}


def _doc(label, benches, system=None, **kw):
    doc = {"label": label, "profile": "standard", "benchmarks": benches,
           "skipped": {},
           "system": system if system is not None else
           {"cpu_model": "Test CPU", "os_family": "linux"},
           "schema": 1, "corpus": {"fixture_hash": "h1"}}
    doc.update(kw)
    return doc


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, rel, text):
        path = os.path.join(self.tmp, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def write_json(self, rel, doc):
        return self.write(rel, json.dumps(doc))


# ---------------------------------------------------------------- F1 + F2

_COLLIDING_NAMES = [
    "work", "bench", "process", "files", "git", "search", "compute",
    "transfer", "ipc", "network", "system", "label", "median", "samples",
    "shell", "python", "node", "diag", "reference", "profile", "runs",
    "meta", "weight", "a",
    # Share's own placeholders and bundled-profile vocabulary.
    "user", "host", "volume", "name", "withheld", "shared", "public",
    "default", "email", "macos", "windows", "linux", "wsl2", "unknown",
    "category", "title", "unit", "method", "scored", "fingerprint",
    "corpus", "tools", "skipped", "anonymized", "workdir",
]


class ShareInvariantRound8Test(unittest.TestCase):
    def test_f1_share_never_moves_scores_for_colliding_names(self):
        from agentperf import scoring
        from agentperf.cli import _all_bundled_references
        from agentperf.privacy import anonymize
        docs = _all_bundled_references()
        self.assertEqual(len(docs), 4)
        anchor = next(d for d in docs
                      if d.get("reference_profile", {}).get("name")
                      == "wsl2-i7-12700k")
        for doc in docs:
            name = doc.get("reference_profile", {}).get("name")
            base = scoring.score(doc, anchor, rounds=1)
            base_lw = scoring.lw100(doc)["seconds"]
            for colliding in _COLLIDING_NAMES:
                with self.subTest(profile=name, colliding=colliding):
                    poisoned = copy.deepcopy(doc)
                    poisoned.setdefault("system", {})["hostname"] = colliding
                    poisoned["system"]["workdir"] = (
                        "/Users/{}/secret project".format(colliding))
                    poisoned["label"] = colliding
                    clean, _changes = anonymize(poisoned)
                    self.assertEqual(
                        set(clean["benchmarks"]), set(doc["benchmarks"]))
                    for bid, entry in doc["benchmarks"].items():
                        got = clean["benchmarks"][bid]
                        self.assertEqual(got["median"], entry["median"], bid)
                        self.assertEqual(got["samples"], entry["samples"], bid)
                    got = scoring.score(clean, anchor, rounds=1)
                    self.assertEqual(got["agentmark"], base["agentmark"])
                    self.assertEqual(got["reach"], base["reach"])
                    self.assertEqual(got["coverage"], base["coverage"])
                    self.assertEqual(scoring.lw100(clean)["seconds"], base_lw)
                    self.assertEqual(clean["fingerprint"],
                                     doc["fingerprint"])
                    self.assertEqual(clean["fingerprint"],
                                     scoring.fingerprint(clean))

    def test_f1_hostname_work_and_bench_direct(self):
        from agentperf.cli import _all_bundled_references
        from agentperf.privacy import anonymize
        win = next(d for d in _all_bundled_references()
                   if d.get("reference_profile", {}).get("name")
                   == "windows-i7-12700k")
        for hostname in ("work", "bench"):
            poisoned = copy.deepcopy(win)
            poisoned["system"]["hostname"] = hostname
            clean, _c = anonymize(poisoned)
            self.assertIn("benchmarks", clean)
            self.assertEqual(len(clean["benchmarks"]),
                             len(win["benchmarks"]), hostname)


class SharePathsRound8Test(unittest.TestCase):
    def test_f2_hyphenated_hostname_fragment_is_scrubbed(self):
        from agentperf.privacy import anonymize
        doc = {"label": "x",
               "system": {"hostname": "alicez-mac.local",
                          "note": "alicez ran this on alicez-mac"},
               "benchmarks": {}}
        clean, _changes = anonymize(doc)
        self.assertNotIn("alicez", json.dumps(clean).lower())

    def test_f2_case_and_apostrophe_paths_do_not_leak(self):
        from agentperf.privacy import anonymize
        system = {"hostname": "BUILDHOST7"}
        system["p1"] = r"C:\users\Alice\Private Projects\file"
        system["p2"] = "c:/users/Alice Smith/Customer List/file"
        system["p3"] = "/Users/O'Brien/Private Repo/file"
        system["p4"] = r"C:\USERS\Alice\Private Projects\file"
        system["p5"] = r"d:\Users\BOB JONES\Customer List\file"
        system["p6"] = "/USERS/Carol O'Neil/Secret Plans/file"
        system["p7"] = "/Home/Dave Smith/Secret Plans/file"
        system["p8"] = r"\\FileServer\TeamShare\Secret Project"
        system["p9"] = "//FileServer/TeamShare/Secret Project"
        system["p10"] = "~alice/Secret Plans"
        system["p11"] = r"~Alice\Secret Plans"
        system["p12"] = "~/Customer List/Plans"
        system["p13"] = "/home/Alice Smith/Secret Plans/file"
        system["p14"] = "/ROOT/CustomerList/db"
        system["p15"] = "/Volumes/Client Merger Documents/file"
        system["p16"] = "mail ALICE.SMITH@Example-Corp.COM please"
        system["p17"] = "addr 10.20.30.40 and 2001:DB8::1234 here"
        system["p18"] = "mac AA:BB:CC:DD:EE:FF and aa-bb-cc-dd-ee-00"
        system["p19"] = r"C:\Documents and Settings\Alice\Secret"
        system["p20"] = "host box1.TS.NET here"
        doc = {"label": "workstation", "system": system, "benchmarks": {}}
        clean, _changes = anonymize(doc)
        dumped = json.dumps(clean).lower()
        for secret in ("alice", "smith", "bob", "jones", "carol", "dave",
                       "o'brien", "brien", "o'neil", "neil",
                       "private projects", "customer list", "private repo",
                       "secret plans", "secret project", "fileserver",
                       "teamshare", "customerlist", "client merger",
                       "example-corp", "10.20.30.40", "2001:db8",
                       "aa:bb:cc", "aa-bb-cc", "box1.ts.net", "buildhost7"):
            self.assertNotIn(secret, dumped, secret)


# ---------------------------------------------------------------- F4

_SAN8 = "sanitized shell env (BASH_ENV, ENV, SHELLOPTS, BASHOPTS removed)"


def _shell_doc(label, shell_policy=None, parallel_policy=None, env=None):
    benches = {}
    for bid, policy in (("process.shell", shell_policy),
                        ("process.parallel", parallel_policy)):
        meta = {"shell_kind": "bash"}
        if policy is not None:
            meta["env_policy"] = policy
        benches[bid] = _entry("process", 2.0, meta=meta)
    kw = {}
    if env is not None:
        kw["measurement_env"] = env
    return _doc(label, benches, **kw)


class ShellPolicyRound8Test(unittest.TestCase):
    def test_f4_shell_launch_read_specifically_not_other_fields(self):
        from agentperf import scoring
        inherited = _shell_doc("a", env={
            "shell_launch": "inherited shell env",
            "node_spawn_diagnostic":
                "sanitized-equivalent (verified unset)"})
        sanitized = _shell_doc("b", env={
            "shell_launch": "sanitized-equivalent (verified unset)",
            "node_spawn_diagnostic": "sanitized-equivalent (verified unset)"})
        self.assertEqual(scoring.shell_env_policy(inherited), "inherited")
        self.assertEqual(scoring.shell_env_policy(sanitized), "sanitized")
        warnings = scoring.compatibility_warnings(inherited, sanitized)
        self.assertTrue(any("shell environment" in w for w in warnings),
                        warnings)

    def test_f4_parallel_policy_compared_independently(self):
        from agentperf import scoring
        a = _shell_doc("a", shell_policy=_SAN8, parallel_policy=_SAN8)
        b = _shell_doc("b", shell_policy=_SAN8,
                       parallel_policy="inherited shell env")
        warnings = scoring.compatibility_warnings(a, b)
        self.assertTrue(any("shell environment" in w for w in warnings),
                        warnings)
        self.assertTrue(any("process.parallel" in w for w in warnings),
                        warnings)

    def test_f4_matching_per_benchmark_policies_do_not_warn(self):
        from agentperf import scoring
        a = _shell_doc("a", shell_policy=_SAN8, parallel_policy=_SAN8)
        b = _shell_doc("b", shell_policy=_SAN8, parallel_policy=_SAN8)
        warnings = scoring.compatibility_warnings(a, b)
        self.assertFalse(any("shell environment" in w for w in warnings),
                         warnings)

    def test_f4_combine_refuses_masked_mismatches(self):
        from agentperf.cli import combine_runs
        inherited = _shell_doc("a", env={
            "shell_launch": "inherited shell env",
            "node_spawn_diagnostic":
                "sanitized-equivalent (verified unset)"})
        sanitized = _shell_doc("a", env={
            "shell_launch": "sanitized-equivalent (verified unset)",
            "node_spawn_diagnostic": "sanitized-equivalent (verified unset)"})
        with self.assertRaises(ValueError):
            combine_runs([inherited, sanitized])
        a = _shell_doc("a", shell_policy=_SAN8, parallel_policy=_SAN8)
        b = _shell_doc("a", shell_policy=_SAN8,
                       parallel_policy="inherited shell env")
        with self.assertRaises(ValueError):
            combine_runs([a, b])


# ---------------------------------------------------------------- F3 + F6

_CHEAT_DUMP = (
    "import inspect\n"
    "import json\n"
    "_real_dump = json.dump\n"
    "_real_dumps = json.dumps\n"
    "def _expected(cases):\n"
    "    return [low if v < low else high if v > high else v\n"
    "            for v, low, high in cases]\n"
    "def _fake_dump(obj, fh, *args, **kw):\n"
    "    cases = inspect.currentframe().f_back.f_locals.get('cases')\n"
    "    return _real_dump(_expected(cases) if cases else obj, fh)\n"
    "def _fake_dumps(obj, *args, **kw):\n"
    "    frame = inspect.currentframe().f_back\n"
    "    cases = frame.f_locals.get('cases')\n"
    "    return _real_dumps(_expected(cases) if cases else obj)\n"
    "json.dump = _fake_dump\n"
    "json.dumps = _fake_dumps\n"
    "def clamp(value, low, high):\n"
    "    return 999\n")


class LiveRound8Test(_Tmp):
    def _repo(self, numbers_body):
        self.write("repo/calcbox/__init__.py", "")
        self.write("repo/calcbox/numbers.py", numbers_body)
        self.write("repo/tests/__init__.py", "")
        return os.path.join(self.tmp, "repo")

    def test_f3_serializer_patch_cannot_manufacture_a_pass(self):
        from agentperf.live import tasks
        repo = self._repo(_CHEAT_DUMP)
        ok, detail = tasks._run_hidden(repo, timeout=60)
        self.assertFalse(ok, detail)

    def test_f3_os_write_patch_cannot_manufacture_a_pass(self):
        from agentperf.live import tasks
        body = ("import json\n"
                "import os\n"
                "os.write = lambda fd, data: len(data)\n"
                "json.dump = lambda obj, fh, *a, **k: fh.write('[1]')\n"
                "def clamp(value, low, high):\n"
                "    return 999\n")
        repo = self._repo(body)
        ok, detail = tasks._run_hidden(repo, timeout=60)
        self.assertFalse(ok, detail)

    def test_f3_correct_clamp_still_passes(self):
        from agentperf.live import tasks
        good = ("def clamp(value, low, high):\n"
                "    return low if value < low else high if value > high "
                "else value\n")
        repo = self._repo(good)
        ok, detail = tasks._run_hidden(repo, timeout=60)
        self.assertTrue(ok, detail)

    def test_f6_keyboard_interrupt_still_cleans_up_group(self):
        from agentperf.live import tasks
        proc = mock.Mock()
        proc.communicate.side_effect = KeyboardInterrupt()
        proc.returncode = None
        with mock.patch("agentperf.live.tasks.subprocess.Popen",
                        return_value=proc), \
                mock.patch("agentperf.live.harness._cleanup_group") as clean:
            with self.assertRaises(KeyboardInterrupt):
                tasks._run_guarded(["python3", "-c", "pass"], self.tmp, 10)
        clean.assert_called_once_with(proc)


# ---------------------------------------------------------------- F5

class MalformedRound8Test(_Tmp):
    def test_f5_meta_list_and_null_weight_rejected_cleanly(self):
        from agentperf import scoring
        shell = _entry("process", 2.0)
        for mutate in ("meta", "weight"):
            entry = dict(shell)
            entry[mutate] = ["bad"] if mutate == "meta" else None
            path = self.write_json(
                "bad-{}.json".format(mutate),
                _doc("bad", {"process.shell": entry}))
            with self.assertRaises(ValueError, msg=mutate):
                scoring.load(path)
            code, out, err = _quiet_main(
                ["compare", path, "--ref", path, "--json=-"])
            self.assertEqual(code, 2, (mutate, out, err))
            self.assertIs(json.loads(out)["ok"], False)
            self.assertNotIn("Traceback", err)

    def _base_doc(self):
        from agentperf import scoring
        benches = {
            "process.shell": _entry("process", 2.0,
                                    meta={"shell_kind": "bash"}),
            "process.parallel": _entry("process", 3.0,
                                       meta={"shell_kind": "bash"}),
            "files.read": _entry("files", 4.0),
        }
        doc = _doc("base", benches, kind="system", suite_version="0.3.0",
                   package_version="0.4.0", profile="quick",
                   started_at="2026-10-13", duration_s=1.0,
                   tools={"git": "/usr/bin/git"},
                   tool_versions={"git": "2.0"},
                   measurement_env={"shell_launch": "sanitized (x)"},
                   scoring_config={"category_weights": {"process": 25.0}})
        doc["fingerprint"] = scoring.fingerprint(doc)
        return doc

    def test_f5_generative_mutations_never_traceback(self):
        base = self._base_doc()
        base_path = self.write_json("base.json", base)
        code, out, err = _quiet_main(
            ["compare", base_path, "--ref", base_path, "--json=-"])
        self.assertEqual(code, 0, (out, err))
        mutants = [None, [], "x", float("nan"), {}]

        def mutations():
            for key in sorted(base):
                for value in mutants:
                    doc = copy.deepcopy(base)
                    doc[key] = copy.deepcopy(value)
                    yield "top." + key, doc
            entry_keys = sorted(base["benchmarks"]["process.shell"])
            for key in entry_keys:
                for value in mutants:
                    doc = copy.deepcopy(base)
                    doc["benchmarks"]["process.shell"][key] = \
                        copy.deepcopy(value)
                    yield "entry." + key, doc

        path = os.path.join(self.tmp, "mutant.json")
        share_out = os.path.join(self.tmp, "mutant-share.json")
        comb_out = os.path.join(self.tmp, "mutant-combined.json")
        html_out = os.path.join(self.tmp, "mutant-report.html")
        for where, doc in mutations():
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            # With --json=-: valid JSON on stdout, exit 0 or 2.
            for argv in (["report", path, "--ref", base_path,
                          "--html", html_out, "--json=-"],
                         ["compare", path, "--ref", base_path,
                          "--json=-"]):
                code, out, err = _quiet_main(argv)
                self.assertIn(code, (0, 2), (where, argv[0], out, err))
                self.assertNotIn("Traceback", err, (where, argv[0]))
                json.loads(out)
            # share / combine take no --json flag; the same rule holds
            # without it, and adding --json=- is a clean usage error
            # whose stdout is still one JSON object.
            for argv in (["share", path, "--out", share_out],
                         ["combine", path, "--out", comb_out]):
                code, out, err = _quiet_main(argv)
                self.assertIn(code, (0, 2), (where, argv[0], out, err))
                self.assertNotIn("Traceback", err, (where, argv[0]))
            for argv in (["share", path, "--json=-"],
                         ["combine", path, "--out", comb_out, "--json=-"]):
                code, out, err = _quiet_main(argv)
                self.assertEqual(code, 2, (where, argv[0], out, err))
                self.assertIs(json.loads(out)["ok"], False)


# ---------------------------------------------------------------- F7

class FetchedRound8Test(unittest.TestCase):
    def test_f7_system_fetch_needs_full_result_shape(self):
        from agentperf.remote import _valid_fetched
        self.assertFalse(_valid_fetched(
            {"label": "my-mac",
             "benchmarks": {"process.shell": {"median": 1.0}}}, "my-mac"))
        full = _doc("my-mac",
                    {"process.shell": _entry("process", 1.0)})
        self.assertTrue(_valid_fetched(full, "my-mac"))
        bad_meta = _doc("my-mac",
                        {"process.shell": _entry("process", 1.0,
                                                 meta=["bad"])})
        self.assertFalse(_valid_fetched(bad_meta, "my-mac"))

    def test_f7_live_fetch_needs_interpretable_runs(self):
        from agentperf.remote import _valid_fetched
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "runs": [{}]}, "my-mac", mode="live"))
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "runs": [{"task": "fix"}]},
            "my-mac", mode="live"))
        run = {"harness": "claude", "task": "fix", "success": True,
               "wall_s": 1.0, "exit_code": 0}
        self.assertTrue(_valid_fetched(
            {"label": "my-mac", "kind": "live", "runs": [run]},
            "my-mac", mode="live"))


# ---------------------------------------------------------------- docs

class DocsRound8Test(unittest.TestCase):
    def _read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return " ".join(fh.read().split())

    def test_docs_skill_race_disclosed(self):
        security = self._read("SECURITY.md")
        self.assertIn("check-then-write", security)

    def test_docs_runner_no_unsupported_assurance(self):
        runner = self._read(os.path.join("agentperf", "runner.py"))
        self.assertNotIn("it holds only", runner)
        self.assertNotIn("holds only regenerable", runner)

    def test_docs_live_claim_is_softened_everywhere(self):
        phrase = "not to resist a deliberately adversarial agent"
        for rel in ("SECURITY.md",
                    os.path.join("docs", "METHODOLOGY.md"),
                    os.path.join("docs", "AGENTS-GUIDE.md"),
                    os.path.join("skills", "agentperf", "references",
                                 "safety-and-privacy.md"),
                    os.path.join("agentperf", "skill", "references",
                                 "safety-and-privacy.md")):
            self.assertIn(phrase, self._read(rel), rel)
        self.assertNotIn("never trusts the",
                         self._read("SECURITY.md"))
        self.assertNotIn("nothing the agent writes can change",
                         self._read(os.path.join("docs", "METHODOLOGY.md")))


if __name__ == "__main__":
    unittest.main()
