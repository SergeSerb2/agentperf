"""Round-7 regression tests: blockers from review-r6-codex-verify.md.

Each test names the finding it pins. Temp folders and mocks only: no
network, no real home directories, no SSH.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
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
    doc = {"label": label, "benchmarks": benches, "skipped": {},
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


class WorkdirRound7Test(_Tmp):
    def test_c1_unmarked_owned_looking_dir_is_refused(self):
        from agentperf import runner
        from agentperf.corpus import Corpus
        work = os.path.join(self.tmp, "work")
        # A real, valid corpus plus a foreign repo/: the old code adopted
        # this directory without ever establishing ownership of repo/.
        Corpus(os.path.join(work, "corpus")).ensure(lambda _m: None)
        keep = self.write("work/repo/keep.txt", "mine")
        with self.assertRaises(SystemExit):
            runner.prepare_workdir(work)
        self.assertFalse(runner.workdir_owned(work))
        with open(keep) as fh:
            self.assertEqual(fh.read(), "mine")

    def test_c1_empty_and_missing_dirs_still_claim(self):
        from agentperf import runner
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        runner.prepare_workdir(empty)
        self.assertTrue(runner.workdir_owned(empty))
        missing = os.path.join(self.tmp, "missing")
        runner.prepare_workdir(missing)
        self.assertTrue(runner.workdir_owned(missing))

    def test_prevnew1_manifest_hash_alone_does_not_own_archive(self):
        from agentperf import runner
        runner.prepare_workdir(self.tmp)
        self.write("corpus/corpus.json",
                   json.dumps({"fixture_hash": "deadbeefdeadbeef"}))
        victim = self.write("corpus-deadbeefdeadbeef.tar.gz", "user backup")
        listed = self.write("corpus-aaaabbbbccccdddd.tar.gz", "ours")
        runner._record_owned_archive(self.tmp, "corpus-aaaabbbbccccdddd.tar.gz")
        runner._clean_owned(self.tmp)
        self.assertTrue(os.path.exists(victim))
        self.assertFalse(os.path.exists(listed))


class SkillRound7Test(_Tmp):
    def _dest(self):
        return os.path.join(self.tmp, "a", "b", "agentperf")

    def _install(self, dest):
        from agentperf import skill as skill_mod
        status, _msg = skill_mod.install_one(dest)
        self.assertEqual(status, "installed")

    def test_f1_uninstall_manifest_traversal_never_escapes(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        self._install(dest)
        keep = self.write("keep.txt", "outside")  # dest/../../../keep.txt
        manifest_path = os.path.join(dest, skill_mod.MANIFEST_NAME)
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["files"]["../../../keep.txt"] = "0" * 64
        manifest["files"]["..\\..\\..\\keep.txt"] = "0" * 64
        manifest["files"]["/etc/passwd"] = "0" * 64
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        status, _msg = skill_mod.uninstall_one(dest, force=True)
        self.assertEqual(status, "removed")
        with open(keep) as fh:
            self.assertEqual(fh.read(), "outside")
        self.assertFalse(os.path.exists(os.path.join(dest, "SKILL.md")))

    def test_f2_forced_uninstall_of_foreign_keeps_extra_files(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        os.makedirs(dest)
        mine = os.path.join(dest, "my-notes.txt")
        with open(mine, "w") as fh:
            fh.write("mine")
        with open(os.path.join(dest, "SKILL.md"), "w") as fh:
            fh.write("someone else's skill")
        status, msg = skill_mod.uninstall_one(dest, force=True)
        self.assertEqual(status, "removed")
        self.assertIn("my-notes.txt", msg)
        with open(mine) as fh:
            self.assertEqual(fh.read(), "mine")
        self.assertFalse(os.path.exists(os.path.join(dest, "SKILL.md")))

    def test_f2_forced_uninstall_of_pure_foreign_refuses(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        os.makedirs(dest)
        mine = os.path.join(dest, "my-notes.txt")
        with open(mine, "w") as fh:
            fh.write("mine")
        status, _msg = skill_mod.uninstall_one(dest, force=True)
        self.assertEqual(status, "refused")
        self.assertTrue(os.path.exists(mine))

    def test_f1_install_refuses_symlinked_managed_file(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        os.makedirs(dest)
        outside = self.write("outside.txt", "precious")
        try:
            os.symlink(outside, os.path.join(dest, "SKILL.md"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        status, _msg = skill_mod.install_one(dest, force=True)
        self.assertEqual(status, "refused")
        with open(outside) as fh:
            self.assertEqual(fh.read(), "precious")

    def test_f1_install_refuses_symlinked_references_dir(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        os.makedirs(dest)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        try:
            os.symlink(elsewhere, os.path.join(dest, "references"),
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        status, _msg = skill_mod.install_one(dest, force=True)
        self.assertEqual(status, "refused")
        self.assertEqual(os.listdir(elsewhere), [])

    def test_f14_uninstall_reports_failed_removals(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        self._install(dest)
        with mock.patch("agentperf.skill.os.remove",
                        side_effect=PermissionError("locked")):
            status, msg = skill_mod.uninstall_one(dest)
        self.assertEqual(status, "failed")
        self.assertIn("SKILL.md", msg)
        self.assertTrue(os.path.exists(os.path.join(dest, "SKILL.md")))

    def test_f14_cli_uninstall_failure_exits_nonzero(self):
        from agentperf import skill as skill_mod
        dest = skill_mod.target_path("claude", project=self.tmp)
        self._install(dest)
        with mock.patch("agentperf.skill.os.remove",
                        side_effect=PermissionError("locked")):
            code, _out, _err = _quiet_main(
                ["skill", "uninstall", "--project", self.tmp,
                 "--target", "claude"])
        self.assertEqual(code, 1)

    def test_f8_malformed_manifest_files_list_does_not_crash(self):
        from agentperf import skill as skill_mod
        dest = self._dest()
        os.makedirs(dest)
        with open(os.path.join(dest, skill_mod.MANIFEST_NAME), "w") as fh:
            json.dump({"skill": "agentperf", "files": ["SKILL.md"]}, fh)
        with open(os.path.join(dest, "SKILL.md"), "w") as fh:
            fh.write("x")
        state, _detail = skill_mod.inspect(dest)
        self.assertEqual(state, "foreign")
        status, _msg = skill_mod.uninstall_one(dest)
        self.assertEqual(status, "refused")


class PrivacyRound7Test(unittest.TestCase):
    def test_f3_review_adversarial_strings(self):
        from agentperf.privacy import anonymize
        doc = {
            "label": "workstation",
            "system": {
                "hostname": "BUILDHOST7",
                "note_unc": r"\\AcmeServer\AcmeShare\Secret Project",
                "note_unc_server": r"at \\AcmeServer now",
                "note_tilde": "~/Customer Acquisition/Plans",
                "note_tilde_user": "~alice/Secret",
                "note_spaced": "/Users/alice Smith/Private Repo/file",
                "note_spaced_home": "/home/Bob Jones/plans",
                "note_case": "ALICE ran it on buildhost7 with Acmeserver",
            },
            "benchmarks": {},
            "/Users/alice Smith/key-leak": "value stays",
        }
        doc["system"].update({
            "note_fwd_unc": "//AcmeServer/share/Secret Plan",
            "note_root": "/root/CustomerList/db",
            "note_legacy": r"C:\Documents and Settings\Alice\Secret",
            "note_tilde_bs": r"~alice\Secret",
            "note_mail": "write to alice.smith@acme-example.com please",
        })
        clean, _changes = anonymize(doc)
        dumped = json.dumps(clean)
        lowered = dumped.lower()
        for secret in ("acmeserver", "acmeshare", "secret project",
                       "customer acquisition", "~alice", "private repo",
                       "bob", "jones", "smith", "buildhost7",
                       "secret plan", "customerlist", "acme-example",
                       "alice.smith"):
            self.assertNotIn(secret, lowered, secret)
        self.assertNotIn("/Users/alice Smith/key-leak", dumped)

    def test_f3_scrubbed_output_still_parses_as_share(self):
        from agentperf.privacy import anonymize
        doc = {"label": "x", "system": {"hostname": "h1-x"},
               "benchmarks": {"process.shell": _entry("process", 1.0)}}
        clean, _changes = anonymize(doc)
        self.assertTrue(clean["anonymized"])
        self.assertIn("process.shell", clean["benchmarks"])


class LiveRound7Test(_Tmp):
    def _repo(self, numbers_body):
        self.write("repo/calcbox/__init__.py", "")
        self.write("repo/calcbox/numbers.py", numbers_body)
        self.write("repo/tests/__init__.py", "")
        return os.path.join(self.tmp, "repo")

    def test_f4_hidden_verifier_cannot_be_patched_by_agent_code(self):
        from agentperf.live import tasks
        evil = (
            "import unittest\n"
            "def _fake_run(self, result=None):\n"
            "    result = result or unittest.TestResult()\n"
            "    result.testsRun += 1\n"
            "    result.addSuccess(self)\n"
            "    return result\n"
            "unittest.TestCase.run = _fake_run\n"
            "def clamp(value, low, high):\n"
            "    return 999\n")
        repo = self._repo(evil)
        ok, _detail = tasks._run_hidden(repo, timeout=60)
        self.assertFalse(ok)

    def test_f4_hidden_verifier_accepts_correct_clamp(self):
        from agentperf.live import tasks
        good = ("def clamp(value, low, high):\n"
                "    return low if value < low else high if value > high else value\n")
        repo = self._repo(good)
        ok, detail = tasks._run_hidden(repo, timeout=60)
        self.assertTrue(ok, detail)

    def test_f4_suite_json_does_not_override_nonzero_exit(self):
        from agentperf.live import tasks
        self._repo("X = 1\n")
        self.write("repo/tests/test_ok.py",
                   "import atexit, os, unittest\n"
                   "atexit.register(lambda: os._exit(3))\n"
                   "class T(unittest.TestCase):\n"
                   "    def test_ok(self):\n"
                   "        self.assertTrue(True)\n")
        repo = os.path.join(self.tmp, "repo")
        ran, ok, _tail = tasks._run_suite(repo, "discover", 60)
        self.assertFalse(ok)
        self.assertEqual(ran, 0)

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_adv_hidden_probe_descendants_are_cleaned_up(self):
        from agentperf.live import tasks
        pidfile = os.path.join(self.tmp, "probe-child.pid")
        body = ("import os, subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', {!r}, {!r}], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "for _i in range(200):\n"
                "    if os.path.exists({!r}):\n"
                "        break\n"
                "    time.sleep(0.05)\n"
                "def clamp(value, low, high):\n"
                "    return low if value < low else high if value > high else value\n").format(
                    "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)",
                    pidfile, pidfile)
        repo = self._repo(body)
        ok, detail = tasks._run_hidden(repo, timeout=60)
        self.assertTrue(ok, detail)
        with open(pidfile) as fh:
            pid = int(fh.read().strip())
        try:
            import time
            for _i in range(200):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                self.fail("probe descendant survived cleanup")
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    @unittest.skipIf(os.name == "nt", "POSIX process groups")
    def test_f5_normal_exit_kills_descendants(self):
        from agentperf.live import harness
        pidfile = self.write("child.pid", "")
        child = ("import os, signal, time\n"
                 "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                 "open({!r}, 'w').write(str(os.getpid()))\n"
                 "time.sleep(60)\n".format(pidfile))
        parent = ("import subprocess, sys, time\n"
                  "subprocess.Popen([sys.executable, '-c', {!r}])\n"
                  "time.sleep(0.5)\n".format(child))
        result = harness.run_streamed(
            "claude", [sys.executable, "-c", parent], "prompt", self.tmp,
            timeout=30)
        with open(pidfile) as fh:
            child_pid = int(fh.read().strip())
        try:
            self.assertEqual(result["exit_code"], 0)
            # Death after SIGKILL is prompt but not instantaneous;
            # poll briefly like the round-6 C15 test does.
            import time
            for _i in range(200):
                try:
                    os.kill(child_pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            else:
                self.fail("descendant survived run_streamed cleanup")
        finally:
            try:
                os.kill(child_pid, 9)
            except OSError:
                pass


_SANITIZED = "sanitized shell env (BASH_ENV, ENV, SHELLOPTS, BASHOPTS removed)"


class ShellPolicyRound7Test(unittest.TestCase):
    def _policy_doc(self, label, policy):
        meta = {"shell_kind": "bash"}
        kw = {}
        if policy in ("sanitized", "inherited"):
            meta["env_policy"] = (_SANITIZED if policy == "sanitized"
                                  else "inherited shell env")
        if policy == "equivalent":
            kw["measurement_env"] = {
                "shell_launch": "sanitized-equivalent (verified unset)"}
        return _doc(label, {"process.shell": _entry("process", 2.0, meta=meta)},
                    **kw)

    def test_f6_differing_policies_warn(self):
        from agentperf import scoring
        warnings = scoring.compatibility_warnings(
            self._policy_doc("a", "sanitized"),
            self._policy_doc("b", "inherited"))
        self.assertTrue(any("shell environment" in w for w in warnings),
                        warnings)

    def test_f6_unknown_policy_warns_against_sanitized(self):
        from agentperf import scoring
        warnings = scoring.compatibility_warnings(
            self._policy_doc("a", "sanitized"),
            self._policy_doc("b", "unknown"))
        self.assertTrue(any("shell environment" in w for w in warnings),
                        warnings)

    def test_f6_equivalent_and_unknown_pairs_do_not_warn(self):
        from agentperf import scoring
        for left, right in (("sanitized", "equivalent"),
                            ("unknown", "unknown"),
                            ("sanitized", "sanitized")):
            warnings = scoring.compatibility_warnings(
                self._policy_doc("a", left), self._policy_doc("b", right))
            self.assertFalse(
                any("shell environment" in w for w in warnings),
                (left, right, warnings))

    def test_f6_bundled_evidenced_profiles_record_equivalence(self):
        from agentperf import scoring
        from agentperf.cli import _all_bundled_references
        docs = {d.get("reference_profile", {}).get("name"): d
                for d in _all_bundled_references()}
        for name in ("wsl2-i7-12700k", "windows-i7-12700k", "macos-m5-pro"):
            self.assertEqual(scoring.shell_env_policy(docs[name]), "sanitized",
                             name)
        # Scores and fingerprints of the bundled files are unchanged.
        anchor = docs["wsl2-i7-12700k"]
        marks = {"wsl2-i7-12700k": 1000, "windows-i7-12700k": 103,
                 "macos-m1-pro": 398, "macos-m5-pro": 408}
        for name, want in marks.items():
            got = scoring.score(docs[name], anchor)["agentmark"]
            self.assertEqual(int(round(got)), want, name)
            self.assertEqual(scoring.fingerprint(docs[name]),
                             docs[name]["fingerprint"], name)


class RemoteRound7Test(_Tmp):
    def _args(self, hosts, **kw):
        args = types.SimpleNamespace(
            hosts=hosts, host=None, wsl=[], label=None, mode="run",
            quick=False, profile="standard", runs=1, workdir=None,
            fetch_tools=False, clean=False, no_network=False, only=None,
            python=None, out_dir=self.tmp, json=None, extra=[])
        for key, value in kw.items():
            setattr(args, key, value)
        return args

    def test_f9_portable_labels(self):
        from agentperf.remote import sanitize_label
        for good in ("my-mac", "my-mac_1.5", "a"):
            self.assertEqual(sanitize_label(good), good)
        for bad in ("CON", "nul", "con.json", "LPT1", "trailing.", "-lead",
                    ".hidden", "../x", "has space", "line\n", "x" * 101, ""):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                sanitize_label(bad)

    def test_f9_case_insensitive_label_collision_refused_early(self):
        from agentperf.remote import parse_targets
        with self.assertRaises(ValueError):
            parse_targets(self._args(["My-Mac", "my-mac"]))

    def test_adv_ssh_option_shaped_host_refused(self):
        from agentperf.remote import parse_targets
        for bad in ("-oProxyCommand=touch-pwned", "-F", "ho st", "h\t1"):
            with self.assertRaises(ValueError, msg=bad):
                parse_targets(self._args([bad]))

    def test_f7_valid_fetched_structure(self):
        from agentperf.remote import _valid_fetched
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "benchmarks": {}}, "my-mac"))
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "benchmarks": {"a": {"median": None}}},
            "my-mac"))
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "benchmarks": {"a": {"median": float("nan")}}},
            "my-mac"))
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "benchmarks": {"a": [1]}}, "my-mac"))
        # Round 8 (F7): system fetches reuse full result validation,
        # so a median-only entry is refused and a full entry is kept.
        self.assertFalse(_valid_fetched(
            {"label": "my-mac", "benchmarks": {"a": {"median": 1.5}}},
            "my-mac"))
        self.assertTrue(_valid_fetched(
            _doc("my-mac", {"a": _entry("process", 1.5)}), "my-mac"))
        self.assertFalse(_valid_fetched(
            {"label": "x", "runs": []}, "x", mode="live"))
        # Round 8 (F7): live runs must be interpretable, not just objects.
        self.assertFalse(_valid_fetched(
            {"label": "x", "runs": [{"task": "fix"}]}, "x", mode="live"))
        self.assertTrue(_valid_fetched(
            {"label": "x", "runs": [{"harness": "claude", "task": "fix",
                                     "success": True, "wall_s": 1.0}]},
            "x", mode="live"))

    def test_f7_operational_exception_does_not_abort_fleet(self):
        from agentperf import remote
        good = {"host": "h2", "wsl": None, "label": "h2", "mode": "run",
                "exit_code": 0, "result_file": "/tmp/h2.json", "error": None}
        calls = []

        def fake_run_one(host, wsl, label, args, pyz, say):
            calls.append(host)
            if host == "h1":
                raise FileNotFoundError("ssh")
            return good

        with mock.patch.object(remote, "build_pyz", return_value="x.pyz"), \
                mock.patch.object(remote, "_run_one", side_effect=fake_run_one):
            code, summary = remote.run_remote(self._args(["h1", "h2"]))
        self.assertEqual(calls, ["h1", "h2"])
        self.assertEqual(len(summary["hosts"]), 2)
        self.assertIn("ssh", summary["hosts"][0]["error"])
        self.assertNotEqual(code, 0)
        self.assertEqual(summary["results"], ["/tmp/h2.json"])

    def test_f10_doctor_record_carries_machine_health(self):
        from agentperf import remote
        args = self._args(["h1"], mode="doctor", json="-")
        doctor_doc = {"command": "doctor", "ok": False,
                      "checks": ["git not found"], "missing_tools": ["git"],
                      "tools": {"git": None}}
        ok_proc = mock.Mock(returncode=0, stdout=b"", stderr=b"")

        def fake_run(argv, **_kw):
            joined = " ".join(str(a) for a in argv)
            if "doctor" in joined and argv[-1].endswith(".json"):
                with open(argv[-1], "w", encoding="utf-8") as fh:
                    json.dump(doctor_doc, fh)
            return ok_proc

        with mock.patch.object(remote, "_is_windows", return_value=False), \
                mock.patch.object(remote, "_ssh", return_value=ok_proc), \
                mock.patch.object(remote.subprocess, "run",
                                  side_effect=fake_run):
            record = remote._run_one("h1", None, "h1", args, "x.pyz",
                                     lambda *a: None)
        self.assertEqual(record["exit_code"], 0)
        self.assertIs(record["doctor"]["ok"], False)
        self.assertEqual(record["doctor"]["missing_tools"], ["git"])
        self.assertEqual(record["doctor"]["checks"], ["git not found"])

    def test_f10_unhealthy_doctor_still_execution_success(self):
        from agentperf import remote
        rec = {"host": "h1", "wsl": None, "label": "h1", "mode": "doctor",
               "exit_code": 0, "result_file": None, "error": None,
               "doctor": {"ok": False, "checks": ["x"], "missing_tools": []}}
        with mock.patch.object(remote, "build_pyz", return_value="x.pyz"), \
                mock.patch.object(remote, "_run_one", return_value=rec):
            code, summary = remote.run_remote(
                self._args(["h1"], mode="doctor"))
        self.assertEqual(code, 0)
        self.assertIs(summary["hosts"][0]["doctor"]["ok"], False)


class JsonRound7Test(_Tmp):
    def _write_result(self, name, doc):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return path

    def test_f8_missing_input_emits_error_json(self):
        code, out, _err = _quiet_main(
            ["compare", os.path.join(self.tmp, "nope.json"), "--json=-"])
        self.assertEqual(code, 2)
        doc = json.loads(out)
        self.assertIs(doc["ok"], False)
        self.assertIn("error", doc)
        self.assertEqual(doc["exit_code"], 2)

    def test_adv_argparse_failure_still_emits_error_json(self):
        code, out, _err = _quiet_main(["compare", "--bogus", "--json=-"])
        self.assertEqual(code, 2)
        self.assertIs(json.loads(out)["ok"], False)

    def test_f8_bad_selector_exit_2_with_error_json(self):
        code, out, _err = _quiet_main(
            ["run", "--only", "nope", "--workdir",
             os.path.join(self.tmp, "w"), "--json=-"])
        self.assertEqual(code, 2)
        doc = json.loads(out)
        self.assertIs(doc["ok"], False)

    def test_f8_load_rejects_malformed_entries(self):
        from agentperf import scoring
        bad_docs = [
            {"benchmarks": {"a": [1, 2]}},
            {"benchmarks": {"a": None}},
            {"benchmarks": {"a": {"median": None}}},
            {"benchmarks": {"a": {"median": "fast"}}},
            {"benchmarks": {"a": {"median": True}}},
            {"benchmarks": {"a": {"median": 1.0}}, "system": [1]},
        ]
        for i, doc in enumerate(bad_docs):
            path = self._write_result("bad%d.json" % i, doc)
            with self.assertRaises(ValueError, msg=repr(doc)):
                scoring.load(path)
        nan_path = self.write("nan.json",
                              '{"benchmarks": {"a": {"median": NaN}}}')
        with self.assertRaises(ValueError):
            scoring.load(nan_path)
        inf_path = self.write("inf.json",
                              '{"benchmarks": {"a": {"median": Infinity}}}')
        with self.assertRaises(ValueError):
            scoring.load(inf_path)

    def test_f8_emit_is_strict_json(self):
        from agentperf.summary import emit
        dest = os.path.join(self.tmp, "s.json")
        with self.assertRaises(ValueError):
            emit({"x": float("nan")}, dest)
        with self.assertRaises(ValueError):
            emit({"x": [float("inf")]}, dest)

    def test_f13_lw100_coverage_in_summaries(self):
        from agentperf.summary import compare_summary, result_summary
        shell = _entry("process", 2.0, meta={"shell_kind": "bash"})
        doc = _doc("a", {"process.shell": dict(shell)})
        ref = _doc("r", {"process.shell": dict(shell)})
        summary = result_summary(doc, ref)
        self.assertEqual(len(summary["lw100_missing_components"]), 7)
        self.assertTrue(any("Shell start" in c for c in
                            summary["lw100_covered_components"]))
        machine = compare_summary([doc], ref)["machines"][0]
        self.assertEqual(len(machine["lw100_missing_components"]), 7)

    def test_f12_missing_default_reference_fails_compare(self):
        path = self._write_result("r.json", _doc(
            "a", {"process.shell": _entry("process", 2.0)}))
        with mock.patch("agentperf.cli._load_reference", return_value=None):
            code, out, _err = _quiet_main(["compare", path, "--json=-"])
        self.assertEqual(code, 2)
        doc = json.loads(out)
        self.assertIs(doc["ok"], False)
        self.assertIn("reference", doc["error"])

    def test_f12_self_reference_needs_explicit_opt_in(self):
        path = self._write_result("r.json", _doc(
            "a", {"process.shell": _entry("process", 2.0)}))
        with mock.patch("agentperf.cli._load_reference", return_value=None):
            code, out, _err = _quiet_main(
                ["compare", path, "--self-reference", "--json=-"])
        self.assertEqual(code, 0, out)
        doc = json.loads(out)
        self.assertEqual(doc["machines"][0]["agentmark"], 1000.0)

    def test_f12_run_fails_before_work_without_reference(self):
        with mock.patch("agentperf.cli._load_reference", return_value=None), \
                mock.patch("agentperf.runner.run_suite") as run_suite:
            code, out, _err = _quiet_main(
                ["run", "--workdir", os.path.join(self.tmp, "w"),
                 "--json=-"])
        self.assertEqual(code, 2)
        run_suite.assert_not_called()
        self.assertIs(json.loads(out)["ok"], False)

    def test_f12_run_no_reference_opt_in(self):
        doc = _doc("a", {"process.shell": _entry("process", 2.0)})
        out_path = os.path.join(self.tmp, "result.json")
        with mock.patch("agentperf.runner.run_suite", return_value=doc):
            code, out, _err = _quiet_main(
                ["run", "--no-reference", "--out", out_path,
                 "--workdir", os.path.join(self.tmp, "w"), "--json=-"])
        self.assertEqual(code, 0, _err)
        summary = json.loads(out)
        self.assertIsNone(summary["reference"])
        self.assertTrue(os.path.isfile(out_path))


class CombineRound7Test(unittest.TestCase):
    def _combinable(self, label, weights=None):
        from agentperf import scoring
        doc = _doc(label, {"process.shell": _entry("process", 2.0),
                           "compute.x": _entry("compute", 3.0)})
        if weights:
            doc["scoring_config"] = scoring.scoring_config_snapshot(weights)
        doc["fingerprint"] = scoring.fingerprint(doc)
        return doc

    def test_f11_combine_rejects_corrupt_stored_fingerprint(self):
        from agentperf.cli import combine_runs
        a = self._combinable("a")
        b = self._combinable("b")
        a["fingerprint"] = b["fingerprint"] = "corrupt-old-fp"
        with self.assertRaises(ValueError) as ctx:
            combine_runs([a, b])
        self.assertIn("fingerprint", str(ctx.exception))

    def test_f11_combine_preserves_recorded_weights(self):
        from agentperf import scoring
        from agentperf.cli import combine_runs
        weights = {"process": 1.0, "compute": 99.0}
        combined = combine_runs([self._combinable("a", weights),
                                 self._combinable("b", weights)])
        self.assertEqual(
            combined["scoring_config"]["category_weights"],
            {"compute": 99.0, "process": 1.0})
        self.assertEqual(combined["fingerprint"],
                         scoring.fingerprint(combined))


class DoctorAndGuideRound7Test(_Tmp):
    def test_f15_low_power_mode_is_a_doctor_check(self):
        system = {"disk_free_gb": 50.0, "logical_cpus": 8,
                  "low_power_mode": True, "os_family": "macos"}
        found = {"git": "/usr/bin/git", "rg": "/usr/bin/rg"}
        with mock.patch("agentperf.env.collect", return_value=system), \
                mock.patch("agentperf.tools.find_tools", return_value=found), \
                mock.patch("agentperf.util.run_text", return_value="git 2.0"):
            code, out, _err = _quiet_main(["doctor", "--json=-"])
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertIs(doc["ok"], False)
        self.assertTrue(any("Low Power Mode" in c for c in doc["checks"]),
                        doc["checks"])

    def test_f15_guide_drift_claims_gone(self):
        guide = os.path.join(ROOT, "docs", "AGENTS-GUIDE.md")
        with open(guide, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("Git Bash as the SSH shell also works", text)
        self.assertNotIn("Reach is the network, not the machine", text)
        for rel in (os.path.join("skills", "agentperf"),
                    os.path.join("agentperf", "skill")):
            base = os.path.join(ROOT, rel)
            for name in os.listdir(base):
                path = os.path.join(base, name)
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as fh:
                        body = fh.read()
                    self.assertNotIn("also works for the POSIX path", body,
                                     path)


if __name__ == "__main__":
    unittest.main()
